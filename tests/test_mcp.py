import asyncio
import json
from pathlib import Path
from typing import cast

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import Tool

import enji_guard_cli.core as core
import enji_guard_cli.mcp_facade as mcp_facade
import enji_guard_cli.mcp_server as mcp_server
from enji_guard_cli.audit import AuditCatalog, parse_audit_catalog
from enji_guard_cli.json_types import JsonObjectPayload
from enji_guard_cli.mcp_server import create_mcp_server
from enji_guard_cli.settings import LogFormat, LogLevelName, TelemetrySettings
from enji_guard_cli.telemetry import configure_logging


def portfolio_payload(
    project_name: str = "MCP Integrations",
    repos: list[dict[str, object]] | None = None,
    active_run_count: int = 0,
) -> dict[str, object]:
    return {
        "observed_at": "2026-07-05T00:00:00Z",
        "summary": {
            "project_count": 1,
            "repo_count": 1,
            "connected_repo_count": 1,
            "active_run_count": active_run_count,
            "recon_done_count": 1,
            "report_complete_count": 0,
        },
        "projects": [
            {
                "project_id": "project_1",
                "project_name": project_name,
                "repos": repos
                if repos is not None
                else [
                    {
                        "project_id": "project_1",
                        "project_name": project_name,
                        "repo_id": "repo_1",
                        "github_owner": "j2h4u",
                        "github_name": "mcp-strava",
                        "github_repo": "j2h4u/mcp-strava",
                        "connected": True,
                        "recon_done": True,
                        "scores": {"tests": 80},
                        "score_grades": {"tests": "good"},
                        "score_summary": {
                            "overall_score": 80.0,
                            "overall_grade": "good",
                            "weakest_axis": "tests",
                            "weakest_score": 80.0,
                            "weakest_grade": "good",
                        },
                        "active_run_count": 0,
                        "active_runs": [],
                        "current_head_sha": "abc123",
                        "last_report_at": None,
                        "reports": empty_report_status(),
                    }
                ],
            }
        ],
    }


def empty_report_status() -> dict[str, object]:
    return {
        "schema_version": 1,
        "repo_id": "repo_1",
        "current_head_sha": "abc123",
        "last_report_at": None,
        "complete": False,
        "fresh": False,
        "readable": False,
        "active": False,
        "queued": False,
        "running": False,
        "missing": True,
        "stale": False,
        "failed": False,
        "counts": {
            "total": 0,
            "readable": 0,
            "active": 0,
            "queued": 0,
            "running": 0,
            "missing": 0,
            "stale": 0,
            "failed": 0,
        },
        "items": [],
    }


def workflow_repo_payload() -> dict[str, object]:
    reports = empty_report_status()
    reports["items"] = [
        {
            "audit": "security",
            "label": "Security",
            "action_key": "audit.security",
            "metric_group": "security",
            "route_slug": "vulns",
            "report": {
                "readability_state": "readable",
                "can_read": True,
                "freshness_state": "stale",
                "current_head_sha": "abc123",
                "audited_head_sha": "def456",
                "created_at": "2026-07-05T00:30:00Z",
                "started_at": "2026-07-05T00:31:00Z",
                "completed_at": "2026-07-05T01:00:00Z",
                "run_status": "completed",
                "fleet_task_id": "fleet_1",
                "stale": True,
            },
            "task": {
                "lifecycle_state": "running",
                "active": True,
                "fleet_task_id": "fleet_2",
                "run_status": "running",
                "created_at": "2026-07-05T01:30:00Z",
                "started_at": "2026-07-05T01:31:00Z",
                "completed_at": None,
            },
            "agent_action": "audit.security",
        }
    ]
    reports.update(
        {
            "repo_id": "repo_1",
            "current_head_sha": "abc123",
            "last_report_at": "2026-07-05T01:00:00Z",
            "complete": False,
            "fresh": False,
            "readable": True,
            "active": True,
            "queued": False,
            "running": True,
            "missing": False,
            "stale": True,
            "failed": False,
            "counts": {
                "total": 1,
                "readable": 1,
                "active": 1,
                "queued": 0,
                "running": 1,
                "missing": 0,
                "stale": 1,
                "failed": 0,
            },
        }
    )
    return {
        "project_id": "project_1",
        "project_name": "MCP Integrations",
        "repo_id": "repo_1",
        "github_owner": "j2h4u",
        "github_name": "mcp-strava",
        "github_repo": "j2h4u/mcp-strava",
        "connected": True,
        "recon_done": True,
        "scores": {"security": 67},
        "score_grades": {"security": "fair"},
        "score_summary": {
            "overall_score": 67.0,
            "overall_grade": "fair",
            "weakest_axis": "security",
            "weakest_score": 67.0,
            "weakest_grade": "fair",
        },
        "active_run_count": 1,
        "active_runs": [{"audit": "security", "run_id": "run_1"}],
        "current_head_sha": "abc123",
        "last_report_at": "2026-07-05T01:00:00Z",
        "reports": reports,
    }


def repo_reports_payload() -> dict[str, object]:
    return {
        "target": {
            "repo_id": "repo_1",
            "project_id": "project_1",
            "github_repo": "j2h4u/mcp-strava",
        },
        "reports": [
            {
                "audit": "security",
                "available": True,
                "current_head_sha": "abc123",
                "last_audited_head_sha": "def456",
                "out_of_date": True,
                "state": "ready",
                "reason": None,
                "message": None,
                "snapshot": {
                    "content": {
                        "summary": {
                            "summary": {
                                "headline": "Refresh this report before acting on it",
                                "score": 67,
                            }
                        }
                    }
                },
            }
        ],
    }


def call_structured_tool(server: FastMCP, name: str, arguments: dict[str, object]) -> object:
    _text, structured = cast(tuple[object, object], asyncio.run(server.call_tool(name, arguments)))
    return structured


def tool_by_name(server: FastMCP, name: str) -> Tool:
    tools = cast(list[Tool], asyncio.run(server.list_tools()))
    return next(tool for tool in tools if tool.name == name)


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
    payload = portfolio_payload()

    def fake_portfolio_overview(project: str | None, sort: str) -> dict[str, object]:
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
    assert captured == {"project": "MCP Integrations", "sort": "weakest"}


def test_mcp_portfolio_overview_fetches_and_parses_one_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog: JsonObjectPayload = {
        "curatedActions": [
            {
                "actionKey": "audit.recon",
                "title": "Recon",
                "category": "workflow",
                "status": "published",
                "runbookKind": "recon",
            },
            {
                "actionKey": "audit.security",
                "title": "Security",
                "category": "audit",
                "status": "published",
                "metricGroup": "vulns",
                "runbookKind": "vuln-audit",
            },
        ]
    }
    calls: dict[str, int] = {"catalog": 0, "parse": 0, "rerun": 0, "links": 0}

    def run_catalog() -> JsonObjectPayload:
        calls["catalog"] += 1
        return catalog

    def parse_catalog(payload: JsonObjectPayload) -> AuditCatalog:
        calls["parse"] += 1
        return parse_audit_catalog(payload)

    monkeypatch.setattr(core, "run_catalog", run_catalog)
    monkeypatch.setattr(core, "_parse_audit_catalog", parse_catalog)
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda _project_id: {
            "project": {"id": "project_1", "name": "MCP Integrations"},
            "repos": [{"id": "repo_1", "githubOwner": "j2h4u", "githubName": "mcp-strava", "connected": True}],
        },
    )
    monkeypatch.setattr(core, "run_repo_active_runs", lambda _repo_id: {"activeRuns": []})
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda _repo_id: (calls.__setitem__("rerun", calls["rerun"] + 1), {"state": {"actions": {}}})[1],
    )
    monkeypatch.setattr(
        core,
        "run_repo_task_links",
        lambda _repo_id: (calls.__setitem__("links", calls["links"] + 1), {"links": []})[1],
    )

    payload = mcp_facade.repository_portfolio_overview(None, "default")

    assert payload["summary"]["repo_count"] == 1
    assert calls == {"catalog": 1, "parse": 1, "rerun": 1, "links": 1}


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
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
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
    }


def test_mcp_read_only_workflow_stays_overview_first_then_repo_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = create_mcp_server()
    overview_tool = tool_by_name(server, "enji_portfolio_overview")
    repo_reports_tool = tool_by_name(server, "enji_repo_reports")

    assert_tool_metadata(overview_tool, repo_reports_tool)

    workflow_repo = workflow_repo_payload()
    overview_payload = portfolio_payload(repos=[workflow_repo], active_run_count=1)
    reports_payload = repo_reports_payload()
    monkeypatch.setattr(mcp_server, "get_portfolio_overview", lambda project, sort: overview_payload)
    monkeypatch.setattr(mcp_server, "get_repo_reports", lambda repo, project: reports_payload)

    repo_entry, report_state = assert_overview_exposes_report_freshness(server)
    assert_repo_reports_preserves_target_and_freshness(server, repo_entry, report_state)


def assert_tool_metadata(overview_tool: Tool, repo_reports_tool: Tool) -> None:
    assert overview_tool.description is not None
    assert "Use this first" in overview_tool.description
    assert "report freshness" in overview_tool.description
    assert repo_reports_tool.description is not None
    assert "Use this after the portfolio overview identifies the target repo" in repo_reports_tool.description
    assert "owner/name or repo_id" in repo_reports_tool.description

    overview_schema = cast(dict[str, object], overview_tool.inputSchema)
    overview_props = cast(dict[str, object], overview_schema["properties"])
    repo_reports_schema = cast(dict[str, object], repo_reports_tool.inputSchema)
    repo_reports_props = cast(dict[str, object], repo_reports_schema["properties"])

    assert set(overview_props) == {"project", "sort"}
    assert cast(dict[str, object], overview_props["project"])["default"] == ""
    assert cast(dict[str, object], overview_props["sort"])["default"] == "default"
    assert cast(dict[str, object], overview_props["sort"])["$ref"] == "#/$defs/RepoSort"
    assert cast(dict[str, object], repo_reports_schema)["required"] == ["repo"]
    assert set(repo_reports_props) == {"project", "repo"}
    assert cast(dict[str, object], repo_reports_props["project"])["default"] == ""


def assert_overview_exposes_report_freshness(server: FastMCP) -> tuple[dict[str, object], dict[str, object]]:
    overview = cast(
        dict[str, object],
        call_structured_tool(server, "enji_portfolio_overview", {"project": "", "sort": "weakest"}),
    )
    project_entry = cast(dict[str, object], cast(list[object], overview["projects"])[0])
    repo_entry = cast(dict[str, object], cast(list[object], project_entry["repos"])[0])
    reports_status = cast(dict[str, object], repo_entry["reports"])
    report_state = cast(
        dict[str, object], cast(dict[str, object], cast(list[object], reports_status["items"])[0])["report"]
    )

    assert repo_entry["repo_id"] == "repo_1"
    assert repo_entry["github_repo"] == "j2h4u/mcp-strava"
    assert reports_status["current_head_sha"] == "abc123"
    assert report_state["freshness_state"] == "stale"
    assert report_state["current_head_sha"] == "abc123"
    assert report_state["audited_head_sha"] == "def456"

    return repo_entry, report_state


def assert_repo_reports_preserves_target_and_freshness(
    server: FastMCP,
    repo_entry: dict[str, object],
    report_state: dict[str, object],
) -> None:
    reports = cast(
        dict[str, object],
        call_structured_tool(
            server,
            "enji_repo_reports",
            {
                "repo": cast(str, repo_entry["github_repo"]),
                "project": cast(str, repo_entry["project_name"]),
            },
        ),
    )
    report_target = cast(dict[str, object], reports["target"])
    report = cast(dict[str, object], cast(list[object], reports["reports"])[0])

    assert report_target["repo_id"] == repo_entry["repo_id"]
    assert report_target["project_id"] == repo_entry["project_id"]
    assert report_target["github_repo"] == repo_entry["github_repo"]
    assert report["out_of_date"] is True
    assert report["current_head_sha"] == report_state["current_head_sha"]
    assert report["last_audited_head_sha"] == report_state["audited_head_sha"]


def test_portfolio_overview_tool_writes_agent_journey_without_raw_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "logs" / "telemetry.jsonl"
    configure_logging(_telemetry_settings(log_file=log_file, log_format="json"), provenance="supervisor")
    server = create_mcp_server()
    payload = portfolio_payload("Pets")

    monkeypatch.setattr(mcp_server, "get_portfolio_overview", lambda project, sort: payload)

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
