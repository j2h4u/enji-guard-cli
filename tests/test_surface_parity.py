import asyncio
from typing import cast

from mcp.types import Tool

from enji_guard_cli.mcp_server import MCP_TOOL_NAMES, create_mcp_server

REQUIRED_MCP_TOOLS = {"enji_portfolio_overview", "enji_repo_reports"}


def test_mcp_tool_names_match_curated_mcp_surface() -> None:
    server = create_mcp_server()

    tools = cast(list[Tool], asyncio.run(server.list_tools()))

    assert [tool.name for tool in tools] == list(MCP_TOOL_NAMES)


def test_registered_mcp_tools_include_portfolio_and_reports() -> None:
    server = create_mcp_server()

    tools = cast(list[Tool], asyncio.run(server.list_tools()))
    registered_tool_names = {tool.name for tool in tools}

    assert registered_tool_names >= REQUIRED_MCP_TOOLS
