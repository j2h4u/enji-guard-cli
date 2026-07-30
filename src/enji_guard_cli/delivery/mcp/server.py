"""Curated read-only MCP adapter.

MCP deliberately exposes only portfolio overview and repository audit reading.
Authentication, scheduling, improvement-job mutation, and every other mutating operation stay in the
CLI/runtime surfaces.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, cast

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from enji_guard_cli.composition import mcp_query_facade
from enji_guard_cli.delivery.presentation import json_projection
from enji_guard_cli.mcp_facade import McpQueryFacade, McpQueryResult
from enji_guard_cli.runtime_observability.journey import AgentJourney, run_agent_journey
from enji_guard_cli.runtime_observability.telemetry import configure_logging
from enji_guard_cli.settings import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    RepositorySortName,
    default_settings,
)

type McpTransport = Literal["stdio", "sse", "streamable-http"]
MCP_TOOL_NAMES = ("enji_portfolio_overview", "enji_repo_audits")


class McpToolContext(Context[ServerSession, McpQueryFacade]):
    """Typed v1 tool context whose lifespan result is the narrow facade."""


def _project_arg(project: str) -> str | None:
    value = project.strip()
    return value or None


def _json(value: object) -> object:
    """Compatibility seam for MCP projection tests and callers."""
    return json_projection(value)


def create_mcp_server(
    host: str = DEFAULT_HTTP_HOST,
    port: int = DEFAULT_HTTP_PORT,
    *,
    auth_file: Path | None = None,
) -> FastMCP:
    """Build the curated MCP server; it composes its own narrow query surface.

    The query surface is deliberately not injectable.  Every caller — the
    container entrypoint included — gets the same read-only composition of
    runner + portfolio + audit, bound to the server lifespan so its pooled
    client is always released.
    """
    settings = default_settings()

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[McpQueryFacade]:
        """Own a composed query surface for exactly as long as the server runs."""
        with mcp_query_facade(auth_file) as owned:
            yield owned

    def query_facade(context: McpToolContext) -> McpQueryFacade:
        try:
            facade = context.request_context.lifespan_context
        except ValueError:
            raise RuntimeError("the MCP query surface exists only while the server lifespan is running") from None
        if facade is None:
            raise RuntimeError("the MCP query surface exists only while the server lifespan is running")
        return cast(McpQueryFacade, facade)

    server = FastMCP(
        name="enji-guard-cli",
        instructions=(
            "Curated read-only Enji Guard surface for portfolio overview and repository audit reading. "
            "Authentication and operator controls belong to the CLI/runtime."
        ),
        host=host,
        port=port,
        lifespan=lifespan,
    )

    @server.tool(
        name=MCP_TOOL_NAMES[0],
        description="Read projects, repositories, scores, and project-level audit activity.",
        structured_output=True,
    )
    async def portfolio_overview(
        context: McpToolContext,
        project: str = "",
        sort: RepositorySortName = settings.repo.default_sort,
    ) -> dict[str, object]:
        result = cast(
            McpQueryResult,
            await asyncio.to_thread(
                run_agent_journey,
                lambda: query_facade(context).portfolio_overview(_project_arg(project), sort),
                AgentJourney(
                    event_prefix="mcp_tool",
                    operation=MCP_TOOL_NAMES[0],
                    surface="mcp",
                    provenance="mcp",
                    selector_kind="project" if project.strip() else "unknown",
                ),
            ),
        )
        return cast(dict[str, object], _json(result.payload))

    @server.tool(
        name=MCP_TOOL_NAMES[1],
        description="Read compact audit status and summaries; name audit selectors to include those Markdown report bodies.",
        structured_output=True,
    )
    async def repository_audits(
        context: McpToolContext,
        repo: str,
        project: str = "",
        audits: list[str] | None = None,
    ) -> dict[str, object]:
        result = cast(
            McpQueryResult,
            await asyncio.to_thread(
                run_agent_journey,
                lambda: query_facade(context).repository_audits(repo.strip(), _project_arg(project), audits),
                AgentJourney(
                    event_prefix="mcp_tool",
                    operation=MCP_TOOL_NAMES[1],
                    surface="mcp",
                    provenance="mcp",
                    selector_kind="repository_locator" if "/" in repo else "repository_id",
                ),
            ),
        )
        return cast(dict[str, object], json_projection(result.payload))

    return server


async def run_mcp_server_async(
    server: object,
    *,
    transport: McpTransport = "stdio",
    mount_path: str | None = None,
) -> None:
    typed_server = cast(FastMCP, server)
    if transport == "stdio":
        await typed_server.run_stdio_async()
    elif transport == "sse":
        await typed_server.run_sse_async(mount_path)
    elif transport == "streamable-http":
        await typed_server.run_streamable_http_async()
    else:
        raise ValueError(f"Unknown transport: {transport}")


def run_mcp_server(
    server: object,
    *,
    transport: McpTransport = "stdio",
    mount_path: str | None = None,
) -> None:
    configure_logging(default_settings().telemetry, provenance="mcp")
    asyncio.run(run_mcp_server_async(server, transport=transport, mount_path=mount_path))


__all__ = [
    "MCP_TOOL_NAMES",
    "McpToolContext",
    "McpTransport",
    "create_mcp_server",
    "run_mcp_server",
    "run_mcp_server_async",
]
