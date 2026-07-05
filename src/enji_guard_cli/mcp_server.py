import asyncio
from collections.abc import Callable
from typing import Literal, TypedDict, cast

from mcp.server.fastmcp import FastMCP

from enji_guard_cli.core import (
    RepoSort,
    read_reports_for_repo,
    runtime_status,
)
from enji_guard_cli.journey import AgentJourney, run_agent_journey
from enji_guard_cli.settings import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT, DEFAULT_REPO_SORT

type McpTransport = Literal["stdio", "sse", "streamable-http"]
type McpToolBody = Callable[[], object]

MCP_TOOL_NAMES: tuple[str, ...] = (
    "enji_portfolio_overview",
    "enji_repo_reports",
)
PORTFOLIO_OVERVIEW_TOOL = MCP_TOOL_NAMES[0]
REPO_REPORTS_TOOL = MCP_TOOL_NAMES[1]

get_portfolio_overview = runtime_status
get_repo_reports = read_reports_for_repo


class PortfolioOverviewPayload(TypedDict):
    observed_at: str
    summary: dict[str, object]
    projects: list[dict[str, object]]


def _project_arg(project: str) -> str | None:
    normalized = project.strip()
    return normalized or None


def _run_mcp_tool(tool_name: str, body: McpToolBody, *, selector_kind: str = "unknown") -> object:
    return run_agent_journey(body, _mcp_journey(tool_name, selector_kind=selector_kind))


async def _run_mcp_tool_thread(tool_name: str, body: McpToolBody, *, selector_kind: str = "unknown") -> object:
    return await asyncio.to_thread(_run_mcp_tool, tool_name, body, selector_kind=selector_kind)


def _mcp_journey(tool_name: str, *, selector_kind: str = "unknown") -> AgentJourney:
    return AgentJourney(
        event_prefix="mcp_tool",
        operation=tool_name,
        surface="mcp",
        provenance="mcp",
        selector_kind=selector_kind,
    )


def _selector_kind_for_repo(repo: str) -> str:
    if "/" in repo:
        return "owner_name"
    if repo.startswith("repo_"):
        return "repo_id"
    return "selector"


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
    asyncio.run(run_mcp_server_async(server, transport=transport, mount_path=mount_path))


def create_mcp_server(host: str = DEFAULT_HTTP_HOST, port: int = DEFAULT_HTTP_PORT) -> FastMCP:
    server = FastMCP(
        name="enji-guard-cli",
        instructions=(
            "Curated read-only Enji Guard surface for portfolio overview and repository report reading. "
            "Use the overview first to see projects, repositories, scores, freshness, and active work. "
            "Use repository reports for a concrete repo. Authentication and operator controls belong to "
            "the Docker runtime and CLI, not MCP."
        ),
        host=host,
        port=port,
    )

    @server.tool(
        name=PORTFOLIO_OVERVIEW_TOOL,
        description=(
            "Get the Enji Guard portfolio overview. Use this first when an agent needs the project list, "
            "repository distribution across projects, scores, report freshness, and active audit work. "
            "Pass project only to narrow the overview to one Enji project; leave it empty for all projects."
        ),
        structured_output=True,
    )
    async def portfolio_overview(
        project: str = "",
        sort: RepoSort = DEFAULT_REPO_SORT,
    ) -> PortfolioOverviewPayload:
        return cast(
            PortfolioOverviewPayload,
            await _run_mcp_tool_thread(
                PORTFOLIO_OVERVIEW_TOOL,
                lambda: get_portfolio_overview(None, _project_arg(project), sort),
                selector_kind="project" if project.strip() else "all",
            ),
        )

    @server.tool(
        name=REPO_REPORTS_TOOL,
        description=(
            "Read all currently readable Enji Guard reports for one repository. Use this after the portfolio "
            "overview identifies the target repo. The repo argument accepts owner/name or repo_id. Pass project "
            "only when the repo selector is ambiguous across Enji projects."
        ),
        structured_output=True,
    )
    async def repo_reports(repo: str, project: str = "") -> dict[str, object]:
        return cast(
            dict[str, object],
            await _run_mcp_tool_thread(
                REPO_REPORTS_TOOL,
                lambda: get_repo_reports(repo.strip(), _project_arg(project), [], all_reports=True),
                selector_kind=_selector_kind_for_repo(repo),
            ),
        )

    return server


server = create_mcp_server()
