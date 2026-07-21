import asyncio
from typing import cast

from mcp.types import Tool

from enji_guard_cli.delivery.mcp.server import MCP_TOOL_NAMES, create_mcp_server

REQUIRED_MCP_TOOLS = {"enji_portfolio_overview", "enji_repo_audits"}


def test_mcp_tool_names_match_curated_mcp_surface() -> None:
    server = create_mcp_server()

    tools = cast(list[Tool], asyncio.run(server.list_tools()))

    assert [tool.name for tool in tools] == list(MCP_TOOL_NAMES)
    assert set(MCP_TOOL_NAMES) == REQUIRED_MCP_TOOLS


def test_registered_mcp_tools_include_portfolio_and_audits() -> None:
    server = create_mcp_server()

    tools = cast(list[Tool], asyncio.run(server.list_tools()))
    registered_tool_names = {tool.name for tool in tools}

    assert registered_tool_names == REQUIRED_MCP_TOOLS
