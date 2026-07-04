import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypedDict, cast

from pytest import MonkeyPatch
from typer.testing import CliRunner

from enji_guard_cli import cli
from enji_guard_cli.audits import AuditAlias
from enji_guard_cli.cli import app
from enji_guard_cli.cli_impl.durations import format_duration_seconds
from enji_guard_cli.core import EmailPreferenceUpdate, ReportWaitOptions, ScheduleSettingsUpdate
from enji_guard_cli.enji_api import EnjiApiError
from enji_guard_cli.readiness import ReadinessVerdict
from enji_guard_cli.settings import TelemetrySettings
from enji_guard_cli.telemetry import configure_logging as configure_test_logging
from enji_guard_cli.telemetry import log_event

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class AuditPayload(TypedDict):
    action_key: str
    alias: str
    job_kind: str | None
    label: str
    route_slug: str | None


class AuthStatusPayload(TypedDict):
    authenticated: bool
    code: str | None
    message: str | None
    auth_file: str
    credential_type: str | None
    email: str | None
    name: str | None
    user_id: str | None


class AccessPayload(TypedDict):
    group: str
    full_access: bool
    limits: dict[str, object]
    usage: list[object]


def _plain_cli_output(value: str) -> str:
    return ANSI_ESCAPE_RE.sub("", value)


def test_serve_runs_mcp_server_with_stdio_defaults(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, int | object | str | None] = {}

    class FakeServer:
        pass

    def fake_create_mcp_server(host: str = "127.0.0.1", port: int = 8000) -> FakeServer:
        captured["host"] = host
        captured["port"] = port
        return FakeServer()

    def fake_run_mcp_server(
        server: FakeServer,
        *,
        transport: str = "stdio",
        mount_path: str | None = None,
    ) -> None:
        captured["server"] = server
        captured["transport"] = transport
        captured["mount_path"] = mount_path

    monkeypatch.setattr(cli, "create_mcp_server", fake_create_mcp_server)
    monkeypatch.setattr(cli, "run_mcp_server", fake_run_mcp_server)

    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8000
    assert isinstance(captured["server"], FakeServer)
    assert captured["transport"] == "stdio"
    assert captured["mount_path"] is None


def test_serve_uses_project_logging_settings(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeServer:
        pass

    def fake_configure_logging(*, provenance: str | None = None) -> None:
        captured["called"] = True
        captured["provenance"] = provenance

    monkeypatch.setattr(cli, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(cli, "create_mcp_server", lambda host="127.0.0.1", port=8000: FakeServer())
    monkeypatch.setattr(cli, "run_mcp_server", lambda server, *, transport="stdio", mount_path=None: None)

    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 0
    assert captured == {"called": True, "provenance": "mcp"}


def test_serve_passes_transport_options_to_mcp_server(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, int | object | str | None] = {}

    class FakeServer:
        pass

    def fake_create_mcp_server(host: str = "127.0.0.1", port: int = 8000) -> FakeServer:
        captured["host"] = host
        captured["port"] = port
        return FakeServer()

    def fake_run_mcp_server(
        server: FakeServer,
        *,
        transport: str = "stdio",
        mount_path: str | None = None,
    ) -> None:
        captured["server"] = server
        captured["transport"] = transport
        captured["mount_path"] = mount_path

    monkeypatch.setattr(cli, "create_mcp_server", fake_create_mcp_server)
    monkeypatch.setattr(cli, "run_mcp_server", fake_run_mcp_server)

    result = CliRunner().invoke(
        app,
        [
            "serve",
            "--transport",
            "sse",
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--mount-path",
            "/events",
            "--allow-external-host",
        ],
    )

    assert result.exit_code == 0
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9000
    assert isinstance(captured["server"], FakeServer)
    assert captured["transport"] == "sse"
    assert captured["mount_path"] == "/events"


def test_run_passes_transport_options_to_supervised_runtime(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, int | str | None] = {}

    def fake_run_service(
        *,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
        mount_path: str | None = None,
    ) -> None:
        captured["transport"] = transport
        captured["host"] = host
        captured["port"] = port
        captured["mount_path"] = mount_path

    monkeypatch.setattr(cli, "run_service", fake_run_service)

    result = CliRunner().invoke(
        app,
        ["run", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000", "--allow-external-host"],
    )

    assert result.exit_code == 0
    assert captured == {
        "transport": "streamable-http",
        "host": "0.0.0.0",
        "port": 9000,
        "mount_path": None,
    }


def test_run_rejects_external_http_bind_without_explicit_allow(monkeypatch: MonkeyPatch) -> None:
    def fake_run_service(
        *,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
        mount_path: str | None = None,
    ) -> None:
        raise AssertionError("external HTTP bind should be rejected before runtime starts")

    monkeypatch.setattr(cli, "run_service", fake_run_service)

    result = CliRunner().invoke(
        app,
        ["run", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"],
    )

    assert result.exit_code == 1
    assert result.stderr == (
        "VALIDATION: HTTP MCP transports may only bind to loopback by default; "
        "pass --allow-external-host to bind externally\n"
    )


def test_health_ready_checks_local_mcp_listener(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeSocket:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_create_connection(address: tuple[str, int], *, timeout: float) -> FakeSocket:
        captured["address"] = address
        captured["timeout"] = timeout
        return FakeSocket()

    monkeypatch.setattr(cli.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(cli, "readiness_verdict", lambda: ReadinessVerdict(ready=True, reason=None, state=None))

    result = CliRunner().invoke(app, ["health", "--ready"])

    assert result.exit_code == 0
    assert result.output == "ready\n"
    assert captured == {"address": ("127.0.0.1", 8000), "timeout": 2.0}


def test_health_ready_fails_when_local_mcp_listener_is_down(monkeypatch: MonkeyPatch) -> None:
    def fake_create_connection(_address: tuple[str, int], *, timeout: float) -> object:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(cli.socket, "create_connection", fake_create_connection)

    result = CliRunner().invoke(app, ["health", "--ready"])

    assert result.exit_code == 1
    assert result.stderr == "UNREADY: MCP listener is not ready at 127.0.0.1:8000: connection refused\n"


def test_health_ready_fails_when_backend_readiness_is_down(monkeypatch: MonkeyPatch) -> None:
    class FakeSocket:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_create_connection(_address: tuple[str, int], *, timeout: float) -> FakeSocket:
        return FakeSocket()

    monkeypatch.setattr(cli.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(
        cli,
        "readiness_verdict",
        lambda: ReadinessVerdict(ready=False, reason="backend readiness failure threshold reached", state=None),
    )

    result = CliRunner().invoke(app, ["health", "--ready"])

    assert result.exit_code == 1
    assert result.stderr == "UNREADY: backend readiness failure threshold reached\n"


def test_version_flag_reports_package_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip()


def test_top_level_help_explains_agent_model() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "projects group GitHub repositories" in result.output
    assert "Recon is" in result.output
    assert "baseline discovery" in result.output
    assert "Text tables are the default" in result.output


def test_command_help_summarizes_workflow_groups() -> None:
    result = CliRunner().invoke(app, ["repo", "--help"])

    assert result.exit_code == 0
    assert "Discover, resolve, add, remove, and move GitHub repositories." in result.output
    assert "List connected repositories with triage scores." in result.output
    assert "Add a GitHub owner/name repository to an Enji project." in result.output
    assert "Remove a repository from an Enji project." in result.output


def test_manual_write_command_help_documents_explicit_scope_flags() -> None:
    schedule = CliRunner().invoke(app, ["schedule", "set", "--help"])
    email = CliRunner().invoke(app, ["email", "set", "--help"])
    schedule_output = _plain_cli_output(schedule.output)
    email_output = _plain_cli_output(email.output)

    assert schedule.exit_code == 0
    assert "Targets: REPO, --project PROJECT --all-repos, or --all-projects." in schedule_output
    assert "--enabled on|off" in schedule_output
    assert "--frequency" in schedule_output
    assert "--timezone TZ" in schedule_output
    assert email.exit_code == 0
    assert "Targets: REPO, --project PROJECT --all-repos, or --all-projects." in email_output
    assert "--manual on|off" in email_output
    assert "--scheduled on|off" in email_output


def test_catalog_audits_reports_canonical_identifier_map() -> None:
    result = CliRunner().invoke(app, ["catalog", "audits", "--json"])

    assert result.exit_code == 0
    audits = cast(list[AuditPayload], json.loads(result.output))
    assert audits[0] == {
        "action_key": "audit.security",
        "alias": "security",
        "job_kind": "vuln-audit",
        "label": "Security",
        "route_slug": "vulns",
    }
    assert {audit["alias"] for audit in audits} == {
        "security",
        "ai-readiness",
        "tests",
        "tech-health",
        "deps",
        "cognitive-debt",
        "dead-code",
        "recon",
    }


def test_catalog_audit_reports_single_alias() -> None:
    result = CliRunner().invoke(app, ["catalog", "audit", "deps", "--json"])

    assert result.exit_code == 0
    audit = cast(AuditPayload, json.loads(result.output))
    assert audit["alias"] == "deps"
    assert audit["route_slug"] == "dependency-hygiene"
    assert audit["job_kind"] == "dependency-hygiene"
    assert audit["action_key"] == "audit.dependency-hygiene"


def test_access_defaults_to_text_and_can_emit_json(monkeypatch: MonkeyPatch) -> None:
    payload: AccessPayload = {
        "group": "pro",
        "full_access": True,
        "limits": {"can_use_schedules": True, "audit_runs": {}, "autofix_runs": {}},
        "usage": [],
    }

    monkeypatch.setattr(cli, "get_access", lambda: payload)

    text = CliRunner().invoke(app, ["access"])
    json_result = CliRunner().invoke(app, ["access", "--json"])

    assert text.exit_code == 0
    assert "group: pro" in text.output
    assert "full_access: yes" in text.output
    assert "can_use_schedules: yes" in text.output
    assert json_result.exit_code == 0
    assert cast(AccessPayload, json.loads(json_result.output)) == payload


def test_access_reports_auth_error_as_json_stderr(monkeypatch: MonkeyPatch) -> None:
    def fake_access() -> object:
        raise EnjiApiError("AUTH_REQUIRED", "auth file does not exist")

    monkeypatch.setattr(cli, "get_access", fake_access)

    result = CliRunner().invoke(app, ["access"])

    assert result.exit_code == 3
    assert result.stderr == "AUTH_REQUIRED: auth file does not exist\n"


def test_report_list_is_not_exposed() -> None:
    result = CliRunner().invoke(app, ["report", "list", "j2h4u/enji-guard-cli"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


def test_project_list_routes_to_core_facade(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "list_projects", lambda: {"projects": [{"id": "project_1"}]})

    result = CliRunner().invoke(app, ["project", "list", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"projects": [{"id": "project_1"}]}


def test_project_create_routes_to_core_facade(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_create(name: str) -> dict[str, object]:
        captured["name"] = name
        return {"project_name": name, "response": {"project": {"id": "project_1"}}}

    monkeypatch.setattr(cli, "create_project", fake_create)

    result = CliRunner().invoke(app, ["project", "create", "Pets", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"project_name": "Pets", "response": {"project": {"id": "project_1"}}}
    assert captured == {"name": "Pets"}


def test_project_rename_routes_to_core_facade(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_rename(project: str, name: str) -> dict[str, object]:
        captured["project"] = project
        captured["name"] = name
        return {"project_id": "project_1", "project_name": name, "response": {}}

    monkeypatch.setattr(cli, "rename_project", fake_rename)

    result = CliRunner().invoke(app, ["project", "rename", "Pets", "Work", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"project_id": "project_1", "project_name": "Work", "response": {}}
    assert captured == {"project": "Pets", "name": "Work"}


def test_project_delete_routes_to_core_facade(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_delete(project: str) -> dict[str, object]:
        captured["project"] = project
        return {"project_id": "project_1", "deleted": True}

    monkeypatch.setattr(cli, "delete_project", fake_delete)

    result = CliRunner().invoke(app, ["project", "delete", "Pets", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"project_id": "project_1", "deleted": True}
    assert captured == {"project": "Pets"}


def test_repo_list_uses_global_project_filter(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_inventory(project: str | None, sort: str = "default") -> dict[str, object]:
        captured["project"] = project
        captured["sort"] = sort
        return {
            "projects": [
                {
                    "project_id": "project_1",
                    "project_name": "Pets",
                    "repos": [
                        {
                            "repo_id": "repo_1",
                            "github_repo": "j2h4u/enji-guard-cli",
                            "connected": True,
                            "recon_done": True,
                            "last_report_at": "2026-06-30T12:00:00Z",
                            "scores": {"vulns": 88, "tech-health": 49},
                            "score_summary": {
                                "overall_score": 68.5,
                                "overall_grade": "fair",
                                "weakest_axis": "tech-health",
                                "weakest_score": 49,
                                "weakest_grade": "poor",
                            },
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(cli, "list_project_inventory", fake_inventory)

    result = CliRunner().invoke(app, ["--project", "Pets", "repo", "list", "--sort", "latest-report"])

    assert result.exit_code == 0
    assert "project  repo" in result.output
    assert "Pets     j2h4u/enji-guard-cli" in result.output
    assert "2026-06-30" in result.output
    assert "tech-health=49" in result.output
    assert captured == {"project": "Pets", "sort": "latest-report"}


def test_repo_list_can_emit_json(monkeypatch: MonkeyPatch) -> None:
    payload = {"projects": []}
    monkeypatch.setattr(cli, "list_project_inventory", lambda project, sort="default": payload)

    result = CliRunner().invoke(app, ["repo", "list", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == payload


def test_repo_resolve_requires_explicit_selector(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_resolve(repo: str, project: str | None) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        return {"resolved": True, "matches": [{"repo_id": "repo_1"}]}

    monkeypatch.setattr(cli, "resolve_repo", fake_resolve)

    missing = CliRunner().invoke(app, ["repo", "resolve", "--json"])
    result = CliRunner().invoke(app, ["repo", "resolve", "j2h4u/enji-guard-cli", "--json"])

    assert missing.exit_code == 2
    assert result.exit_code == 0
    assert json.loads(result.output)["resolved"] is True
    assert captured == {"repo": "j2h4u/enji-guard-cli", "project": None}


def test_repo_add_uses_global_project_filter(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_add(github_repo: str, project: str | None) -> dict[str, object]:
        captured["github_repo"] = github_repo
        captured["project"] = project
        return {"repo": {"id": "repo_1"}}

    monkeypatch.setattr(cli, "add_repo", fake_add)

    result = CliRunner().invoke(app, ["--project", "Pets", "repo", "add", "j2h4u/enji-guard-cli", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"repo": {"id": "repo_1"}}
    assert captured == {"github_repo": "j2h4u/enji-guard-cli", "project": "Pets"}


def test_repo_add_human_output_suggests_next_steps(monkeypatch: MonkeyPatch) -> None:
    def fake_add(_github_repo: str, _project: str | None) -> dict[str, object]:
        return {
            "added": True,
            "connected": False,
            "repo": {"repo": {"githubOwner": "j2h4u", "githubName": "enji-guard-cli"}},
        }

    monkeypatch.setattr(cli, "add_repo", fake_add)

    result = CliRunner().invoke(app, ["repo", "add", "j2h4u/enji-guard-cli"])

    assert result.exit_code == 0
    assert "next: enji-guard status REPO" in result.output
    assert "next: if recon_done=false, run enji-guard recon start REPO" in result.output


def test_repo_remove_uses_global_project_filter(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_remove(repo: str, project: str | None) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        return {"repo_id": "repo_1", "removed": True}

    monkeypatch.setattr(cli, "remove_repo", fake_remove)

    result = CliRunner().invoke(app, ["--project", "Pets", "repo", "remove", "j2h4u/enji-guard-cli", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"repo_id": "repo_1", "removed": True}
    assert captured == {"repo": "j2h4u/enji-guard-cli", "project": "Pets"}


def test_repo_move_uses_global_source_project_and_destination_option(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_move(repo: str, source_project: str | None, target_project: str) -> dict[str, object]:
        captured["repo"] = repo
        captured["source_project"] = source_project
        captured["target_project"] = target_project
        return {
            "source_project_id": "project_1",
            "target_project_id": "project_2",
            "repo": {"repo_id": "repo_1", "github_repo": "j2h4u/enji-guard-cli"},
            "preflight": {"ok": True},
            "response": {"repo": {"id": "repo_1"}},
        }

    monkeypatch.setattr(cli, "move_repo", fake_move)

    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "repo", "move", "j2h4u/enji-guard-cli", "--to-project", "Work", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["target_project_id"] == "project_2"
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "source_project": "Pets",
        "target_project": "Work",
    }


def test_status_routes_to_runtime_snapshot(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_status(repo: str | None, project: str | None, sort: str = "default") -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["sort"] = sort
        return {
            "summary": {"repo_count": 1},
            "projects": [
                {
                    "project_id": "project_1",
                    "project_name": "Pets",
                    "repos": [
                        {
                            "repo_id": "repo_1",
                            "github_repo": "j2h4u/enji-guard-cli",
                            "connected": True,
                            "recon_done": True,
                            "scores": {"tech-health": 49},
                            "score_summary": {
                                "overall_score": 49,
                                "overall_grade": "poor",
                                "weakest_axis": "tech-health",
                                "weakest_score": 49,
                                "weakest_grade": "poor",
                            },
                            "reports": {
                                "counts": {
                                    "total": 2,
                                    "readable": 2,
                                    "active": 1,
                                    "queued": 0,
                                    "running": 1,
                                    "missing": 0,
                                    "stale": 1,
                                    "failed": 0,
                                },
                                "items": [
                                    {
                                        "audit": "security",
                                        "report": {
                                            "readability_state": "readable",
                                            "freshness_state": "fresh",
                                            "current_head_sha": "0307f239c88a4c761cd2f96cb17b5eb8a4ae8487",
                                            "audited_head_sha": "0307f239c88a4c761cd2f96cb17b5eb8a4ae8487",
                                            "completed_at": "2026-06-30T12:00:00Z",
                                            "stale": False,
                                        },
                                        "task": {
                                            "lifecycle_state": "none",
                                            "run_status": None,
                                            "completed_at": None,
                                        },
                                    },
                                    {
                                        "audit": "tests",
                                        "report": {
                                            "readability_state": "readable",
                                            "freshness_state": "stale",
                                            "current_head_sha": "0307f239c88a4c761cd2f96cb17b5eb8a4ae8487",
                                            "audited_head_sha": "3249b095c88a4c761cd2f96cb17b5eb8a4ae8487",
                                            "completed_at": "2026-06-29T12:00:00Z",
                                            "stale": True,
                                        },
                                        "task": {
                                            "lifecycle_state": "running",
                                            "run_status": "in_progress",
                                            "completed_at": None,
                                        },
                                    },
                                ],
                            },
                            "active_run_count": 0,
                            "last_report_at": "2026-06-30T12:00:00Z",
                            "current_head_sha": "0307f239c88a4c761cd2f96cb17b5eb8a4ae8487",
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr(cli, "runtime_status", fake_status)

    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "status", "j2h4u/enji-guard-cli", "--sort", "overall"],
    )

    assert result.exit_code == 0
    assert "project  repo" in result.output
    assert "readable  stale  active  queued  running  failed" in result.output
    assert "2         tests  1       0       1        0" in result.output
    assert "tests" in result.output
    assert "mixed" in result.output
    assert "2026-06-30" in result.output
    assert "0307f239" in result.output
    assert "security  readable  fresh" in result.output
    assert "tests     readable  stale      running  in_progress" in result.output
    assert captured == {"repo": "j2h4u/enji-guard-cli", "project": "Pets", "sort": "overall"}


def test_recon_start_routes_to_workflow_facade(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_start(repo: str, project: str | None) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        return {"task": {"id": "task_recon"}}

    monkeypatch.setattr(cli, "start_recon", fake_start)

    result = CliRunner().invoke(app, ["--project", "Pets", "recon", "start", "j2h4u/enji-guard-cli", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"task": {"id": "task_recon"}}
    assert captured == {"repo": "j2h4u/enji-guard-cli", "project": "Pets"}


def test_audit_start_routes_positional_report_audits(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_start(
        repo: str,
        project: str | None,
        audits: list[AuditAlias],
        *,
        all_reports: bool,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["audits"] = [audit.value for audit in audits]
        captured["all_reports"] = all_reports
        return {"results": []}

    monkeypatch.setattr(cli, "start_report_audits", fake_start)

    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "audit", "start", "j2h4u/enji-guard-cli", "security", "tests", "--json"],
    )

    assert result.exit_code == 0
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "project": "Pets",
        "audits": ["security", "tests"],
        "all_reports": False,
    }


def test_audit_start_routes_all_flag(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_start(
        repo: str,
        project: str | None,
        audits: list[AuditAlias],
        *,
        all_reports: bool,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["audits"] = [audit.value for audit in audits]
        captured["all_reports"] = all_reports
        return {"results": []}

    monkeypatch.setattr(cli, "start_report_audits", fake_start)

    result = CliRunner().invoke(app, ["audit", "start", "j2h4u/enji-guard-cli", "--all", "--json"])

    assert result.exit_code == 0
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "project": None,
        "audits": [],
        "all_reports": True,
    }


def test_audit_start_human_output_shows_preflight_warning(monkeypatch: MonkeyPatch) -> None:
    def fake_start(
        repo: str,
        project: str | None,
        audits: list[AuditAlias],
        *,
        all_reports: bool,
    ) -> dict[str, object]:
        return {
            "target": {"repo_id": "repo_1", "project_id": "project_1", "github_repo": "j2h4u/enji-guard-cli"},
            "preflight": {
                "warning": {
                    "code": "SNAPSHOT_VISIBILITY_RISK",
                    "message": "starting report audits can temporarily hide older snapshots",
                },
                "counts": {"ready": 2, "running": 1, "stale": 1},
                "lists": {"ready": ["security", "tests"], "running": ["deps"], "stale": ["tests"]},
            },
            "results": [{"audit": "security", "action_key": "audit.security", "state": "started"}],
        }

    monkeypatch.setattr(cli, "start_report_audits", fake_start)

    result = CliRunner().invoke(app, ["audit", "start", "j2h4u/enji-guard-cli", "security"])

    assert result.exit_code == 0
    assert "preflight: 2 ready, 1 running, 1 stale" in _plain_cli_output(result.output)
    assert (
        "warning: SNAPSHOT_VISIBILITY_RISK starting report audits can temporarily hide older snapshots"
        in _plain_cli_output(result.output)
    )
    assert "results: started=1, queued=0, already_running=0, up_to_date=0, failed=0" in _plain_cli_output(result.output)


def test_audit_start_json_output_returns_structured_preflight(monkeypatch: MonkeyPatch) -> None:
    def fake_start(
        repo: str,
        project: str | None,
        audits: list[AuditAlias],
        *,
        all_reports: bool,
    ) -> dict[str, object]:
        return {
            "target": {"repo_id": "repo_1", "project_id": "project_1", "github_repo": "j2h4u/enji-guard-cli"},
            "preflight": {
                "warning": {
                    "code": "SNAPSHOT_VISIBILITY_RISK",
                    "message": "starting report audits can temporarily hide older snapshots",
                },
                "counts": {"ready": 2, "running": 1, "stale": 1},
                "lists": {"ready": ["security", "tests"], "running": ["deps"], "stale": ["tests"]},
            },
            "results": [{"audit": "security", "action_key": "audit.security", "state": "started"}],
        }

    monkeypatch.setattr(cli, "start_report_audits", fake_start)

    result = CliRunner().invoke(app, ["audit", "start", "j2h4u/enji-guard-cli", "security", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["preflight"] == {
        "warning": {
            "code": "SNAPSHOT_VISIBILITY_RISK",
            "message": "starting report audits can temporarily hide older snapshots",
        },
        "counts": {"ready": 2, "running": 1, "stale": 1},
        "lists": {"ready": ["security", "tests"], "running": ["deps"], "stale": ["tests"]},
    }


def test_wait_routes_to_top_level_workflow(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_wait(
        repo: str,
        project: str | None,
        *,
        options: ReportWaitOptions,
        heartbeat: object,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["poll_seconds"] = options.poll_seconds
        captured["timeout_seconds"] = options.timeout_seconds
        captured["heartbeat_seconds"] = options.heartbeat_seconds
        captured["heartbeat"] = callable(heartbeat)
        return {
            "complete": True,
            "reason": "complete",
            "counts": {"ready": 7, "running": 0, "missing": 0, "stale": 1},
            "stale": ["security"],
        }

    monkeypatch.setattr(cli, "wait_for_reports", fake_wait)

    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "wait", "j2h4u/enji-guard-cli", "--timeout", "30s", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["complete"] is True
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "project": "Pets",
        "poll_seconds": 30,
        "timeout_seconds": 30,
        "heartbeat_seconds": 120,
        "heartbeat": True,
    }


def test_wait_rejects_old_single_audit_shape() -> None:
    result = CliRunner().invoke(app, ["wait", "repo_1", "security", "--timeout", "30s"])

    assert result.exit_code == 2


def test_wait_rejects_unknown_option() -> None:
    result = CliRunner().invoke(app, ["wait", "repo_1", "--unexpected"])

    assert result.exit_code == 2
    assert "No such option: --unexpected" in _plain_cli_output(result.stderr)


def test_wait_exits_two_when_timeout_payload_is_not_complete(monkeypatch: MonkeyPatch) -> None:
    def fake_wait(_repo: str, _project: str | None, **_kwargs: object) -> dict[str, object]:
        return {
            "complete": False,
            "reason": "timeout",
            "counts": {"ready": 6, "running": 0, "missing": 1, "stale": 6},
        }

    monkeypatch.setattr(cli, "wait_for_reports", fake_wait)

    result = CliRunner().invoke(app, ["wait", "repo_1", "--timeout", "30s", "--json"])

    assert result.exit_code == 2
    assert json.loads(result.output)["complete"] is False


def test_wait_heartbeat_writes_stderr_without_polluting_json_stdout(monkeypatch: MonkeyPatch) -> None:
    def fake_wait(
        _repo: str,
        _project: str | None,
        **kwargs: object,
    ) -> dict[str, object]:
        heartbeat = cast(Callable[[dict[str, object]], None], kwargs["heartbeat"])
        heartbeat(
            {
                "elapsed_seconds": 120,
                "current_head_sha": "abc123",
                "counts": {"ready": 6, "running": 1, "missing": 0, "stale": 3},
            }
        )
        return {"complete": True, "reason": "complete", "counts": {"ready": 7, "running": 0, "missing": 0, "stale": 0}}

    monkeypatch.setattr(cli, "wait_for_reports", fake_wait)

    result = CliRunner().invoke(app, ["wait", "repo_1", "--timeout", "30s", "--json"])

    assert result.exit_code == 0
    assert result.stderr == (
        'wait heartbeat: elapsed_seconds=120 elapsed_human="2m" ready=6 running=1 missing=0 stale=3 '
        "current_head_sha=abc123\n"
    )
    assert json.loads(result.stdout)["complete"] is True


def test_wait_routes_transport_info_logs_to_file_not_operator_stderr(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_wait(
        _repo: str,
        _project: str | None,
        **kwargs: object,
    ) -> dict[str, object]:
        heartbeat = cast(Callable[[dict[str, object]], None], kwargs["heartbeat"])
        log_event(
            logging.getLogger("enji_guard_cli.transport"),
            logging.INFO,
            "enji_http_response",
            {"operation": "report status"},
        )
        heartbeat(
            {
                "elapsed_seconds": 120,
                "current_head_sha": "abc123",
                "counts": {"ready": 6, "running": 1, "missing": 0, "stale": 3},
            }
        )
        return {"complete": True, "reason": "complete", "counts": {"ready": 7, "running": 0, "missing": 0, "stale": 0}}

    monkeypatch.setattr(cli, "wait_for_reports", fake_wait)
    log_file = tmp_path / "logs" / "telemetry.jsonl"

    def configure_logging_to_file(*, provenance: str | None = None) -> None:
        configure_test_logging(
            TelemetrySettings(
                level_name="INFO",
                log_format="json",
                log_file=log_file,
                max_bytes=10_000,
                backup_count=1,
            ),
            provenance=provenance,
        )

    monkeypatch.setattr(cli, "configure_logging", configure_logging_to_file)

    result = CliRunner().invoke(
        app,
        [
            "wait",
            "repo_1",
            "--timeout",
            "30s",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert result.stderr == (
        'wait heartbeat: elapsed_seconds=120 elapsed_human="2m" ready=6 running=1 missing=0 stale=3 '
        "current_head_sha=abc123\n"
    )
    assert "enji_http_response" not in result.stderr
    transport_log = next(line for line in _telemetry_log_lines(log_file) if line["message"] == "enji_http_response")
    assert transport_log["provenance"] == "cli"


def test_cli_journey_telemetry_logs_start_and_finish_for_all_flagged_command(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "logs" / "telemetry.jsonl"

    def fake_start(
        repo: str,
        project: str | None,
        audits: list[object],
        *,
        all_reports: bool,
    ) -> dict[str, object]:
        assert repo == "j2h4u/enji-guard-cli"
        assert project == "Pets"
        assert audits == []
        assert all_reports is True
        return {"results": [{"audit": "security", "action_key": "audit.security", "state": "started"}]}

    monkeypatch.setattr(cli, "start_report_audits", fake_start)
    monkeypatch.setattr(
        cli,
        "configure_logging",
        lambda *, provenance=None: configure_test_logging(
            TelemetrySettings(
                level_name="INFO",
                log_format="json",
                log_file=log_file,
                max_bytes=10_000,
                backup_count=1,
            ),
            provenance=provenance,
        ),
    )

    result = CliRunner().invoke(app, ["--project", "Pets", "audit", "start", "j2h4u/enji-guard-cli", "--all", "--json"])

    assert result.exit_code == 0
    assert result.stderr == ""

    started, finished = _telemetry_log_lines(log_file)
    assert started["message"] == "cli_command_started"
    assert started["provenance"] == "cli"
    assert isinstance(started["command_path"], str)
    assert started["command_path"] == "enji-guard audit start"
    assert started["json"] is True
    assert started["all"] is True
    assert started["selector_kind"] == "all"
    assert finished["message"] == "cli_command_finished"
    assert finished["command_path"] == started["command_path"]
    assert finished["json"] is True
    assert finished["all"] is True
    assert finished["selector_kind"] == "all"
    assert finished["exit_code"] == 0
    assert isinstance(finished["duration_ms"], int)
    assert finished["result_count"] == 1


def test_cli_journey_telemetry_logs_validation_failures_to_finish_event(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "logs" / "telemetry.jsonl"
    monkeypatch.setattr(
        cli,
        "configure_logging",
        lambda *, provenance=None: configure_test_logging(
            TelemetrySettings(
                level_name="INFO",
                log_format="json",
                log_file=log_file,
                max_bytes=10_000,
                backup_count=1,
            ),
            provenance=provenance,
        ),
    )

    monkeypatch.setattr(cli, "delete_project", lambda project: {"project_id": "project_1", "deleted": True})

    result = CliRunner().invoke(app, ["project", "delete", "Pets"])

    assert result.exit_code == 0
    assert result.output == "project_id: project_1\ndeleted: yes\n"
    assert result.stderr == ""

    started, finished = _telemetry_log_lines(log_file)
    assert started["message"] == "cli_command_started"
    assert started["provenance"] == "cli"
    assert isinstance(started["command_path"], str)
    assert started["command_path"] == "enji-guard project delete"
    assert started["json"] is False
    assert started["selector_kind"] == "project"
    assert finished["message"] == "cli_command_finished"
    assert finished["command_path"] == started["command_path"]
    assert finished["json"] is False
    assert finished["selector_kind"] == "project"
    assert finished["exit_code"] == 0
    assert "Pets" not in log_file.read_text(encoding="utf-8")


def _telemetry_log_lines(log_file: Path) -> list[dict[str, object]]:
    return [cast(dict[str, object], json.loads(line)) for line in log_file.read_text(encoding="utf-8").splitlines()]


def _telemetry_settings(log_file: Path, log_format: Literal["json", "text"] = "json") -> TelemetrySettings:
    return TelemetrySettings(
        level_name="INFO",
        log_format=log_format,
        log_file=log_file,
        max_bytes=10_000,
        backup_count=1,
    )


def test_duration_formatting_uses_readable_largest_units() -> None:
    assert format_duration_seconds(-1) == "0s"
    assert format_duration_seconds(11) == "11s"
    assert format_duration_seconds(131) == "2m 11s"
    assert format_duration_seconds(300) == "5m"
    assert format_duration_seconds(301) == "5m"
    assert format_duration_seconds(3661) == "1h 1m"
    assert format_duration_seconds(183_845) == "2d 3h"


def test_report_show_is_not_exposed() -> None:
    result = CliRunner().invoke(app, ["report", "show", "j2h4u/enji-guard-cli", "security"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


def test_report_read_defaults_to_ready_reports_and_markdown(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_read_reports(
        repo: str,
        project: str | None,
        audits: list[AuditAlias],
        *,
        all_reports: bool,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["audits"] = [audit.value for audit in audits]
        captured["all_reports"] = all_reports
        return {
            "reports": [
                {"audit": "security", "snapshot": {"content": {"report": "# Security\n"}}},
                {"audit": "tests", "snapshot": {"content": {"report": "# Tests\n"}}},
            ]
        }

    monkeypatch.setattr(cli, "read_reports_for_repo", fake_read_reports)

    result = CliRunner().invoke(app, ["--project", "Pets", "report", "read", "j2h4u/enji-guard-cli"])

    assert result.exit_code == 0
    assert result.output == (
        "<!-- enji-report audit=security -->\n\n# Security\n\n---\n\n<!-- enji-report audit=tests -->\n\n# Tests\n"
    )
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "project": "Pets",
        "audits": [],
        "all_reports": False,
    }


def test_report_read_can_emit_json_for_all_reports(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_read_reports(
        repo: str,
        project: str | None,
        audits: list[AuditAlias],
        *,
        all_reports: bool,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["audits"] = [audit.value for audit in audits]
        captured["all_reports"] = all_reports
        return {
            "reports": [
                {
                    "audit": "security",
                    "current_head_sha": "head_2",
                    "last_audited_head_sha": "head_1",
                    "out_of_date": True,
                    "available": True,
                    "state": "ready",
                    "reason": None,
                    "message": None,
                    "snapshot": {
                        "content": {
                            "completedAt": "2026-06-30T12:00:00Z",
                            "summary": {"summary": {"headline": "Security is clean", "score": 98}},
                            "report": "# Security",
                        }
                    },
                },
                {
                    "audit": "cognitive-debt",
                    "current_head_sha": "head_2",
                    "last_audited_head_sha": None,
                    "out_of_date": None,
                    "available": False,
                    "state": "missing",
                    "reason": "missing",
                    "message": "cognitive-debt report is missing",
                },
            ]
        }

    monkeypatch.setattr(cli, "read_reports_for_repo", fake_read_reports)

    result = CliRunner().invoke(
        app,
        ["report", "read", "j2h4u/enji-guard-cli", "--all", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "reports": [
            {
                "audit": "security",
                "available": True,
                "completed_at": "2026-06-30T12:00:00Z",
                "current_head_sha": "head_2",
                "error_code": None,
                "headline": "Security is clean",
                "last_audited_head_sha": "head_1",
                "message": None,
                "out_of_date": True,
                "reason": None,
                "score": 98,
                "state": "ready",
            },
            {
                "audit": "cognitive-debt",
                "available": False,
                "completed_at": None,
                "current_head_sha": "head_2",
                "error_code": None,
                "headline": None,
                "last_audited_head_sha": None,
                "message": "cognitive-debt report is missing",
                "out_of_date": None,
                "reason": "missing",
                "score": None,
                "state": "missing",
            },
        ]
    }
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "project": None,
        "audits": [],
        "all_reports": True,
    }


def test_report_read_markdown_marks_unavailable_reports(monkeypatch: MonkeyPatch) -> None:
    def fake_read_reports(
        repo: str,
        project: str | None,
        audits: list[AuditAlias],
        *,
        all_reports: bool,
    ) -> dict[str, object]:
        return {
            "reports": [
                {"audit": "security", "snapshot": {"content": {"report": "# Security\n"}}},
                {
                    "audit": "cognitive-debt",
                    "available": False,
                    "state": "missing",
                    "reason": "missing",
                    "message": "cognitive-debt report is missing",
                },
            ]
        }

    monkeypatch.setattr(cli, "read_reports_for_repo", fake_read_reports)

    result = CliRunner().invoke(app, ["report", "read", "j2h4u/enji-guard-cli", "--all"])

    assert result.exit_code == 0
    assert result.output == (
        "<!-- enji-report audit=security -->\n\n# Security\n\n---\n\n"
        "<!-- enji-report audit=cognitive-debt unavailable=true -->\n\n"
        "_cognitive-debt report is missing_\n"
    )


def test_report_read_markdown_strips_terminal_control_sequences(monkeypatch: MonkeyPatch) -> None:
    def fake_read_reports(
        repo: str,
        project: str | None,
        audits: list[AuditAlias],
        *,
        all_reports: bool,
    ) -> dict[str, object]:
        return {
            "reports": [
                {
                    "audit": "security",
                    "snapshot": {
                        "content": {"report": "# Security\n\x1b[31mred\x1b[0m\n\x1b]52;c;Y2xpcGJvYXJk\x07\nbad\btext\n"}
                    },
                },
                {
                    "audit": "tests",
                    "available": False,
                    "state": "running",
                    "reason": "running",
                    "message": "\x1b[2Jtests report is still running",
                },
            ]
        }

    monkeypatch.setattr(cli, "read_reports_for_repo", fake_read_reports)

    result = CliRunner().invoke(app, ["report", "read", "j2h4u/enji-guard-cli", "--all"])

    assert result.exit_code == 0
    assert result.output == (
        "<!-- enji-report audit=security -->\n\n# Security\nred\n\nbadtext\n\n---\n\n"
        "<!-- enji-report audit=tests unavailable=true -->\n\n"
        "_tests report is still running_\n"
    )


def test_report_read_rejects_unknown_option() -> None:
    result = CliRunner().invoke(app, ["report", "read", "j2h4u/enji-guard-cli", "--unexpected"])

    assert result.exit_code == 2
    assert "No such option: --unexpected" in _plain_cli_output(result.stderr)


def test_schedule_list_defaults_to_text_table(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_list(repo: str | None, project: str | None) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        return {
            "schedules": [
                {
                    "project_name": "Pets",
                    "github_repo": "j2h4u/enji-guard-cli",
                    "audit": "security",
                    "enabled": True,
                    "frequency": "weekly",
                    "days_of_week": ["mon"],
                    "schedule_time_source": "auto",
                    "schedule_time": "09:00",
                    "timezone": "UTC",
                }
            ],
            "summary": {"repo_count": 1, "audit_count": 1},
        }

    monkeypatch.setattr(cli, "list_schedule_settings", fake_list)

    result = CliRunner().invoke(app, ["--project", "Pets", "schedule", "list", "j2h4u/enji-guard-cli"])

    assert result.exit_code == 0
    assert "project  repo" in result.output
    assert "Pets     j2h4u/enji-guard-cli" in result.output
    assert "security" in result.output
    assert "weekly" in result.output
    assert "09:00 (auto)" in result.output
    assert captured == {"repo": "j2h4u/enji-guard-cli", "project": "Pets"}


def test_schedule_list_warns_about_timezone_divergence(monkeypatch: MonkeyPatch) -> None:
    def fake_list(_repo: str | None, _project: str | None) -> dict[str, object]:
        return {
            "schedules": [
                {
                    "github_repo": "j2h4u/enji-guard-cli",
                    "audit": "security",
                    "enabled": True,
                    "timezone": "Asia/Almaty",
                },
                {
                    "github_repo": "j2h4u/enji-guard-cli",
                    "audit": "cognitive-debt",
                    "enabled": True,
                    "timezone": "UTC",
                },
            ],
            "summary": {"repo_count": 1, "audit_count": 2},
        }

    monkeypatch.setattr(cli, "list_schedule_settings", fake_list)

    result = CliRunner().invoke(app, ["schedule", "list", "j2h4u/enji-guard-cli"])

    assert result.exit_code == 0
    assert "timezone divergence: j2h4u/enji-guard-cli" in result.output
    assert "Asia/Almaty: security" in result.output
    assert "UTC: cognitive-debt" in result.output


def test_schedule_list_can_emit_json(monkeypatch: MonkeyPatch) -> None:
    payload = {"schedules": [], "summary": {"repo_count": 0, "audit_count": 0}}
    monkeypatch.setattr(cli, "list_schedule_settings", lambda repo, project: payload)

    result = CliRunner().invoke(app, ["schedule", "list", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == payload


def test_schedule_set_routes_batch_update(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_set(
        repo: str | None,
        project: str | None,
        update: ScheduleSettingsUpdate,
        *,
        all_repos: bool = False,
        all_projects: bool = False,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["enabled"] = update.enabled
        captured["frequency"] = update.frequency
        captured["all_repos"] = all_repos
        captured["all_projects"] = all_projects
        return {
            "schedules": [
                {
                    "project_name": "Pets",
                    "github_repo": "j2h4u/enji-guard-cli",
                    "audit": "security",
                    "enabled": True,
                    "frequency": "weekly-2x",
                    "days_of_week": ["mon", "thu"],
                    "schedule_time_source": "user",
                    "schedule_time": "09:30",
                    "timezone": "Asia/Almaty",
                    "status": "changed",
                }
            ],
            "summary": {"repo_count": 1, "audit_count": 1, "changed_count": 1},
        }

    monkeypatch.setattr(cli, "set_schedule_settings", fake_set)

    result = CliRunner().invoke(
        app,
        [
            "--project",
            "Pets",
            "schedule",
            "set",
            "j2h4u/enji-guard-cli",
            "--enabled",
            "on",
            "--frequency",
            "weekly-2x",
        ],
    )

    assert result.exit_code == 0
    assert "status" in result.output
    assert "changed" in result.output
    assert "09:30 (manual)" in result.output
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "project": "Pets",
        "enabled": True,
        "frequency": "weekly-2x",
        "all_repos": False,
        "all_projects": False,
    }


def test_schedule_set_routes_timezone_batch_update(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_set(
        repo: str | None,
        project: str | None,
        update: ScheduleSettingsUpdate,
        *,
        all_repos: bool = False,
        all_projects: bool = False,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["enabled"] = update.enabled
        captured["frequency"] = update.frequency
        captured["schedule_time"] = update.schedule_time
        captured["timezone"] = update.timezone
        captured["all_repos"] = all_repos
        captured["all_projects"] = all_projects
        return {"schedules": [], "summary": {"repo_count": 1, "audit_count": 7}}

    monkeypatch.setattr(cli, "set_schedule_settings", fake_set)

    result = CliRunner().invoke(
        app,
        [
            "--project",
            "Pets",
            "schedule",
            "set",
            "j2h4u/enji-guard-cli",
            "--timezone",
            "Asia/Almaty",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["summary"] == {"repo_count": 1, "audit_count": 7}
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "project": "Pets",
        "enabled": None,
        "frequency": None,
        "schedule_time": None,
        "timezone": "Asia/Almaty",
        "all_repos": False,
        "all_projects": False,
    }


def test_schedule_timezone_command_is_not_exposed() -> None:
    result = CliRunner().invoke(app, ["schedule", "timezone", "Asia/Almaty"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


def test_schedule_auto_time_routes_batch_update(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_set(
        repo: str | None,
        project: str | None,
        update: ScheduleSettingsUpdate,
        *,
        all_repos: bool = False,
        all_projects: bool = False,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["enabled"] = update.enabled
        captured["frequency"] = update.frequency
        captured["schedule_time"] = update.schedule_time
        captured["timezone"] = update.timezone
        captured["all_repos"] = all_repos
        captured["all_projects"] = all_projects
        return {"schedules": [], "summary": {"repo_count": 3, "audit_count": 21}}

    monkeypatch.setattr(cli, "set_schedule_settings", fake_set)

    result = CliRunner().invoke(app, ["schedule", "auto-time", "--all-projects", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["summary"] == {"repo_count": 3, "audit_count": 21}
    assert captured == {
        "repo": None,
        "project": None,
        "enabled": None,
        "frequency": None,
        "schedule_time": "auto",
        "timezone": None,
        "all_repos": False,
        "all_projects": True,
    }


def test_schedule_set_rejects_unknown_option() -> None:
    result = CliRunner().invoke(app, ["schedule", "set", "j2h4u/enji-guard-cli", "--unexpected", "weekly-2x"])

    assert result.exit_code == 1
    assert result.stderr == "VALIDATION: unknown option: --unexpected\n"


def test_schedule_set_can_emit_json(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_set(
        repo: str | None,
        project: str | None,
        update: ScheduleSettingsUpdate,
        *,
        all_repos: bool = False,
        all_projects: bool = False,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["enabled"] = update.enabled
        captured["all_repos"] = all_repos
        captured["all_projects"] = all_projects
        return {"schedules": [], "summary": {"repo_count": 1, "audit_count": 7}}

    monkeypatch.setattr(cli, "set_schedule_settings", fake_set)

    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "schedule", "set", "--all-repos", "--enabled", "off", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"schedules": [], "summary": {"repo_count": 1, "audit_count": 7}}
    assert captured == {
        "repo": None,
        "project": "Pets",
        "enabled": False,
        "all_repos": True,
        "all_projects": False,
    }


def test_email_list_defaults_to_text_table(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "list_email_preferences",
        lambda repo, project: {
            "preferences": [
                {
                    "project_name": "Pets",
                    "github_repo": "j2h4u/enji-guard-cli",
                    "audit": "security",
                    "manual_run_completion": True,
                    "scheduled_run_completion": False,
                }
            ],
            "summary": {"repo_count": 1, "audit_count": 1},
        },
    )

    result = CliRunner().invoke(app, ["email", "list"])

    assert result.exit_code == 0
    assert "project  repo" in result.output
    assert "Pets     j2h4u/enji-guard-cli" in result.output
    assert "security" in result.output
    assert "yes" in result.output
    assert "no" in result.output


def test_email_set_routes_batch_update(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_set(
        repo: str | None,
        project: str | None,
        update: EmailPreferenceUpdate,
        *,
        all_repos: bool = False,
        all_projects: bool = False,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        captured["manual"] = update.manual_run_completion
        captured["auto"] = update.scheduled_run_completion
        captured["all_repos"] = all_repos
        captured["all_projects"] = all_projects
        return {
            "preferences": [
                {
                    "project_name": "Pets",
                    "github_repo": "j2h4u/enji-guard-cli",
                    "audit": "security",
                    "manual_run_completion": True,
                    "scheduled_run_completion": False,
                }
            ],
            "summary": {"repo_count": 1, "audit_count": 1},
        }

    monkeypatch.setattr(cli, "set_email_preferences", fake_set)

    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "email", "set", "--all-repos", "--scheduled", "off", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["summary"] == {"repo_count": 1, "audit_count": 1}
    assert captured == {
        "repo": None,
        "project": "Pets",
        "manual": None,
        "auto": False,
        "all_repos": True,
        "all_projects": False,
    }


def test_email_set_rejects_unknown_option() -> None:
    result = CliRunner().invoke(app, ["email", "set", "j2h4u/enji-guard-cli", "--unexpected", "off"])

    assert result.exit_code == 1
    assert result.stderr == "VALIDATION: unknown option: --unexpected\n"


def test_auth_status_reports_text_and_zero_exit_code(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"

    def fake_auth_status(path: Path | None) -> AuthStatusPayload:
        assert path == auth_file
        return {
            "authenticated": True,
            "code": None,
            "message": None,
            "auth_file": str(auth_file),
            "credential_type": "bearer_token",
            "email": "user@example.com",
            "name": "User",
            "user_id": "user_1",
        }

    monkeypatch.setattr(cli, "auth_status", fake_auth_status)

    result = CliRunner().invoke(app, ["auth", "status", "--auth-file", str(auth_file)])

    assert result.exit_code == 0
    assert "authenticated: yes" in result.output
    assert "credential_type: bearer_token" in result.output
    assert "email: user@example.com" in result.output


def test_auth_status_can_emit_json(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"

    def fake_auth_status(path: Path | None) -> AuthStatusPayload:
        assert path == auth_file
        return {
            "authenticated": True,
            "code": None,
            "message": None,
            "auth_file": str(auth_file),
            "credential_type": "bearer_token",
            "email": "user@example.com",
            "name": "User",
            "user_id": "user_1",
        }

    monkeypatch.setattr(cli, "auth_status", fake_auth_status)

    result = CliRunner().invoke(app, ["auth", "status", "--auth-file", str(auth_file), "--json"])

    assert result.exit_code == 0
    payload = cast(AuthStatusPayload, json.loads(result.output))
    assert payload["authenticated"] is True
    assert payload["credential_type"] == "bearer_token"


def test_auth_status_reports_json_and_exit_code_three_when_unauthenticated(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    auth_file = tmp_path / "auth.json"

    def fake_auth_status(path: Path | None) -> AuthStatusPayload:
        assert path == auth_file
        return {
            "authenticated": False,
            "code": "AUTH_REQUIRED",
            "message": "auth file does not exist",
            "auth_file": str(auth_file),
            "credential_type": None,
            "email": None,
            "name": None,
            "user_id": None,
        }

    monkeypatch.setattr(cli, "auth_status", fake_auth_status)

    result = CliRunner().invoke(app, ["auth", "status", "--auth-file", str(auth_file)])

    assert result.exit_code == 3
    assert "authenticated: no" in result.output
    assert "code: AUTH_REQUIRED" in result.output


def test_auth_refresh_reports_json(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    payload = {
        "ok": True,
        "auth_file": str(auth_file),
        "credential_type": "cookie",
        "cookie_count": 6,
        "access_expires_at": "2026-06-29T12:15:00+00:00",
    }

    def fake_refresh_auth(path: Path | None) -> dict[str, object]:
        assert path == auth_file
        return payload

    monkeypatch.setattr(cli, "refresh_auth", fake_refresh_auth)

    result = CliRunner().invoke(app, ["auth", "refresh", "--auth-file", str(auth_file), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == payload


def test_auth_status_awaits_async_result_for_json(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"

    async def fake_auth_status(path: Path | None) -> AuthStatusPayload:
        assert path == auth_file
        return {
            "authenticated": True,
            "code": None,
            "message": None,
            "auth_file": str(auth_file),
            "credential_type": "cookie",
            "email": None,
            "name": None,
            "user_id": None,
        }

    monkeypatch.setattr(cli, "auth_status", fake_auth_status)

    result = CliRunner().invoke(app, ["auth", "status", "--auth-file", str(auth_file), "--json"])

    assert result.exit_code == 0
    payload = cast(AuthStatusPayload, json.loads(result.output))
    assert payload["authenticated"] is True
    assert payload["credential_type"] == "cookie"
