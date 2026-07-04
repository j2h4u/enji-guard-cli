import asyncio
from pathlib import Path
from typing import cast

from mcp.types import Tool

from enji_guard_cli.core import READ_OPERATION_SPECS
from enji_guard_cli.mcp_server import MCP_TOOL_NAMES_BY_OPERATION, create_mcp_server

REQUIRED_OPERATION_TOOLS = {
    "access": "enji_access",
    "reports_list": "enji_reports_list",
}

PROJECT_ADMIN_COMMANDS = (
    "enji-guard project create NAME",
    "enji-guard project rename PROJECT NAME",
    "enji-guard project delete PROJECT",
    "enji-guard repo remove REPO",
    "enji-guard repo move REPO --to-project PROJECT",
)


def test_core_operation_specs_match_registered_mcp_tool_names() -> None:
    server = create_mcp_server()

    tools = cast(list[Tool], asyncio.run(server.list_tools()))

    assert [tool.name for tool in tools] == [MCP_TOOL_NAMES_BY_OPERATION[spec.name] for spec in READ_OPERATION_SPECS]
    assert [tool.description for tool in tools] == [spec.summary for spec in READ_OPERATION_SPECS]


def test_required_operation_specs_include_access_and_report_list() -> None:
    spec_tools = {spec.name.value: MCP_TOOL_NAMES_BY_OPERATION[spec.name] for spec in READ_OPERATION_SPECS}

    assert {name: spec_tools.get(name) for name in REQUIRED_OPERATION_TOOLS} == REQUIRED_OPERATION_TOOLS


def test_registered_mcp_tools_include_access_and_report_list() -> None:
    server = create_mcp_server()

    tools = cast(list[Tool], asyncio.run(server.list_tools()))
    registered_tool_names = {tool.name for tool in tools}

    assert set(REQUIRED_OPERATION_TOOLS.values()) <= registered_tool_names


def test_cli_surface_docs_stay_aligned_on_project_admin_commands() -> None:
    design = _normalized_text(Path("docs/cli-surface-design.md"))
    spec = _normalized_text(Path("docs/enji-cli-mcp-spec.md"))

    for command in PROJECT_ADMIN_COMMANDS:
        assert command in design
        assert command in spec


def _normalized_text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())
