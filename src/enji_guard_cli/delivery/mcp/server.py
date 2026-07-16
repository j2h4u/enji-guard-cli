"""Curated read-only MCP adapter.

MCP deliberately exposes only portfolio overview and repository audit reading.
Authentication, scheduling, autofix, and every mutating operation stay in the
CLI/runtime surfaces.
"""

import asyncio
from typing import Literal, cast

from mcp.server.fastmcp import FastMCP

from enji_guard_cli.application import Application
from enji_guard_cli.portfolio.status import PortfolioStatus
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
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    fields = getattr(value, "__dataclass_fields__", None)
    if isinstance(fields, dict):
        return {name: _json(cast(object, getattr(value, name))) for name in fields}
    return value


def create_mcp_server(
    host: str = DEFAULT_HTTP_HOST,
    port: int = DEFAULT_HTTP_PORT,
    *,
    application: Application | None = None,
) -> FastMCP:
    app = application or Application.from_auth_file()
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
        description="Read projects, repositories, scores, audit freshness, and active audit work.",
        structured_output=True,
    )
    async def portfolio_overview(
        project: str = "",
        sort: RepositorySortName = DEFAULT_REPO_SORT,
    ) -> dict[str, object]:
        portfolio = cast(
            PortfolioStatus,
            await asyncio.to_thread(
                run_agent_journey,
                lambda: app.portfolio_status(sort),
                AgentJourney(
                    event_prefix="mcp_tool",
                    operation=MCP_TOOL_NAMES[0],
                    surface="mcp",
                    provenance="mcp",
                    selector_kind="project" if project.strip() else "unknown",
                ),
            ),
        )
        selected = _project_arg(project)
        projects = [
            item
            for item in portfolio.projects
            if selected is None or selected in {item.project.project_id, item.project.name}
        ]
        return cast(dict[str, object], _json({"observed_at": portfolio.observed_at, "projects": projects}))

    @server.tool(
        name=MCP_TOOL_NAMES[1],
        description="Read currently available audit artifacts and typed freshness for one repository.",
        structured_output=True,
    )
    async def repository_audits(repo: str, project: str = "") -> dict[str, object]:
        payload = await asyncio.to_thread(
            run_agent_journey,
            lambda: app.audit_read(
                repo.strip(),
                project=_project_arg(project),
                all_audits=True,
            ),
            AgentJourney(
                event_prefix="mcp_tool",
                operation=MCP_TOOL_NAMES[1],
                surface="mcp",
                provenance="mcp",
                selector_kind="owner_name" if "/" in repo else "repo_id",
            ),
        )
        return cast(dict[str, object], _json(payload))

    return server


async def run_mcp_server_async(
    server: FastMCP,
    *,
    transport: McpTransport = "stdio",
    mount_path: str | None = None,
) -> None:
    if transport == "stdio":
        await server.run_stdio_async()
    elif transport == "sse":
        await server.run_sse_async(mount_path)
    elif transport == "streamable-http":
        await server.run_streamable_http_async()
    else:
        raise ValueError(f"Unknown transport: {transport}")


def run_mcp_server(
    server: FastMCP,
    *,
    transport: McpTransport = "stdio",
    mount_path: str | None = None,
) -> None:
    configure_logging(default_settings().telemetry, provenance="mcp")
    asyncio.run(run_mcp_server_async(server, transport=transport, mount_path=mount_path))


__all__ = ["MCP_TOOL_NAMES", "McpTransport", "create_mcp_server", "run_mcp_server", "run_mcp_server_async"]
