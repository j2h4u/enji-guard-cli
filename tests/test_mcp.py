import asyncio
import json
from pathlib import Path
from typing import cast

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import Tool

import enji_guard_cli.mcp_server as mcp_server
from enji_guard_cli.mcp_server import create_mcp_server
from enji_guard_cli.settings import LogFormat, LogLevelName, TelemetrySettings
from enji_guard_cli.telemetry import configure_logging


def call_structured_tool(server: FastMCP, name: str, arguments: dict[str, object]) -> object:
    _text, structured = cast(tuple[object, object], asyncio.run(server.call_tool(name, arguments)))
    return structured


def test_create_mcp_server_registers_expected_tools() -> None:
    server = create_mcp_server()

    tools = cast(list[Tool], asyncio.run(server.list_tools()))
    names = {tool.name for tool in tools}

    assert names == {
        "enji_portfolio_overview",
        "enji_repo_reports",
    }


def test_mcp_surface_omits_noisy_control_plane_tools() -> None:
    server = create_mcp_server()

    tools = cast(list[Tool], asyncio.run(server.list_tools()))
    names = {tool.name for tool in tools}

    assert "enji_auth_status" not in names
    assert "enji_catalog_audit" not in names
    assert "enji_access" not in names
    assert "enji_catalog_audits" not in names
    assert "enji_reports_list" not in names


def test_run_mcp_server_async_runs_streamable_http_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_mcp_server()
    called = False

    async def fake_run_streamable_http_async() -> None:
        nonlocal called

        called = True

    monkeypatch.setattr(server, "run_streamable_http_async", fake_run_streamable_http_async)

    asyncio.run(mcp_server.run_mcp_server_async(server, transport="streamable-http"))

    assert called is True


def test_portfolio_overview_tool_returns_runtime_status(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_mcp_server()
    captured: dict[str, object | None] = {}
    payload: dict[str, object] = {
        "observed_at": "2026-07-05T00:00:00Z",
        "summary": {"project_count": 1, "repo_count": 1},
        "projects": [
            {
                "project_id": "project_1",
                "project_name": "MCP Integrations",
                "repos": [
                    {
                        "repo_id": "repo_1",
                        "github_repo": "j2h4u/mcp-strava",
                        "scores": {"tests": 80},
                    }
                ],
            }
        ],
    }

    def fake_portfolio_overview(repo: str | None, project: str | None, sort: str) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["sort"] = sort
        return payload

    monkeypatch.setattr(mcp_server, "get_portfolio_overview", fake_portfolio_overview)

    structured = call_structured_tool(
        server,
        "enji_portfolio_overview",
        {"project": "MCP Integrations", "sort": "weakest"},
    )

    assert structured == payload
    assert captured == {"repo": None, "project": "MCP Integrations", "sort": "weakest"}


def test_repo_reports_tool_reads_all_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_mcp_server()
    captured: dict[str, object | None] = {}
    payload: dict[str, object] = {
        "target": {"repo_id": "repo_1", "github_repo": "j2h4u/mcp-strava"},
        "reports": [{"audit": "tests", "available": True, "snapshot": {"content": {"report": "ok"}}}],
    }

    def fake_repo_reports(
        repo: str,
        project: str | None,
        audits: list[object],
        *,
        all_reports: bool,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["audits"] = audits
        captured["all_reports"] = all_reports
        return payload

    monkeypatch.setattr(mcp_server, "get_repo_reports", fake_repo_reports)

    structured = call_structured_tool(
        server,
        "enji_repo_reports",
        {"repo": "j2h4u/mcp-strava", "project": "MCP Integrations"},
    )

    assert structured == payload
    assert captured == {
        "repo": "j2h4u/mcp-strava",
        "project": "MCP Integrations",
        "audits": [],
        "all_reports": True,
    }


def test_portfolio_overview_tool_writes_agent_journey_without_raw_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "logs" / "telemetry.jsonl"
    configure_logging(_telemetry_settings(log_file=log_file, log_format="json"), provenance="supervisor")
    server = create_mcp_server()
    payload: dict[str, object] = {
        "observed_at": "2026-07-05T00:00:00Z",
        "summary": {"project_count": 1, "repo_count": 1},
        "projects": [{"project_id": "project_1", "project_name": "Pets", "repos": []}],
    }

    monkeypatch.setattr(mcp_server, "get_portfolio_overview", lambda repo, project, sort: payload)

    structured = call_structured_tool(server, "enji_portfolio_overview", {"project": "Pets"})

    assert structured == payload
    started, finished = _telemetry_log_lines(log_file)
    assert started["message"] == "mcp_tool_started"
    assert started["provenance"] == "mcp"
    assert started["surface"] == "mcp"
    assert started["tool_name"] == "enji_portfolio_overview"
    assert started["operation"] == "enji_portfolio_overview"
    assert started["selector_kind"] == "project"
    assert finished["message"] == "mcp_tool_finished"
    assert finished["exit_code"] == 0
    assert finished["result_count"] == 1
    assert "Pets" not in log_file.read_text(encoding="utf-8")


def _telemetry_log_lines(log_file: Path) -> list[dict[str, object]]:
    return [cast(dict[str, object], json.loads(line)) for line in log_file.read_text(encoding="utf-8").splitlines()]


def _telemetry_settings(
    *,
    log_file: Path | None,
    log_format: LogFormat,
    level_name: LogLevelName = "INFO",
) -> TelemetrySettings:
    return TelemetrySettings(
        level_name=level_name,
        log_format=log_format,
        log_file=log_file,
        max_bytes=10_000,
        backup_count=1,
    )
