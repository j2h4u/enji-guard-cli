"""Curated read-only MCP adapter.

MCP deliberately exposes only portfolio overview and repository audit reading.
Authentication, scheduling, autofix, and every mutating operation stay in the
CLI/runtime surfaces.
"""

import asyncio
from datetime import date
from pathlib import Path
from typing import Literal, cast

from mcp.server.fastmcp import FastMCP

from enji_guard_cli.composition import create_mcp_query_facade
from enji_guard_cli.mcp_facade import McpQueryFacade, McpQueryResult
from enji_guard_cli.runtime_observability.journey import AgentJourney, run_agent_journey
from enji_guard_cli.runtime_observability.telemetry import configure_logging
from enji_guard_cli.settings import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    DEFAULT_REPO_SORT,
    RepositorySortName,
    default_settings,
)

type McpTransport = Literal["stdio", "sse", "streamable-http"]
MCP_TOOL_NAMES = ("enji_portfolio_overview", "enji_repo_audits")


def _project_arg(project: str) -> str | None:
    value = project.strip()
    return value or None


def _json(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, Path)):
        return value.isoformat() if isinstance(value, date) else str(value)
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    fields = getattr(value, "__dataclass_fields__", None)
    if isinstance(fields, dict):
        return {name: _json(cast(object, getattr(value, name))) for name in fields}
    return str(value)


def create_mcp_server(
    host: str = DEFAULT_HTTP_HOST,
    port: int = DEFAULT_HTTP_PORT,
    *,
    queries: McpQueryFacade | None = None,
) -> FastMCP:
    query_facade = queries or create_mcp_query_facade()
    server = FastMCP(
        name="enji-guard-cli",
        instructions=(
            "Curated read-only Enji Guard surface for portfolio overview and repository audit reading. "
            "Authentication and operator controls belong to the CLI/runtime."
        ),
        host=host,
        port=port,
    )

    @server.tool(
        name=MCP_TOOL_NAMES[0],
        description="Read projects, repositories, scores, and project-level audit activity.",
        structured_output=True,
    )
    async def portfolio_overview(
        project: str = "",
        sort: RepositorySortName = DEFAULT_REPO_SORT,
    ) -> dict[str, object]:
        result = cast(
            McpQueryResult,
            await asyncio.to_thread(
                run_agent_journey,
                lambda: query_facade.portfolio_overview(_project_arg(project), sort),
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
        description="Read currently available audit artifacts and typed freshness for one repository.",
        structured_output=True,
    )
    async def repository_audits(repo: str, project: str = "") -> dict[str, object]:
        result = cast(
            McpQueryResult,
            await asyncio.to_thread(
                run_agent_journey,
                lambda: query_facade.repository_audits(repo.strip(), _project_arg(project)),
                AgentJourney(
                    event_prefix="mcp_tool",
                    operation=MCP_TOOL_NAMES[1],
                    surface="mcp",
                    provenance="mcp",
                    selector_kind="owner_name" if "/" in repo else "repo_id",
                ),
            ),
        )
        return cast(dict[str, object], _json(result.payload))

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


__all__ = ["MCP_TOOL_NAMES", "McpTransport", "create_mcp_server", "run_mcp_server", "run_mcp_server_async"]
