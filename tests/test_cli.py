import json
from pathlib import Path
from typing import TypedDict, cast

from pytest import MonkeyPatch
from typer.testing import CliRunner

from enji_guard_cli import cli
from enji_guard_cli.cli import app
from enji_guard_cli.core import AuditAlias
from enji_guard_cli.enji_api import EnjiApiError


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


class ReportsListPayload(TypedDict):
    projects: list[dict[str, object]]


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


def test_serve_does_not_override_log_format_environment(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    class FakeServer:
        pass

    def fake_configure_logging(log_level: str | None = None, log_format: str | None = None) -> None:
        captured["log_level"] = log_level
        captured["log_format"] = log_format

    monkeypatch.setattr(cli, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(cli, "create_mcp_server", lambda host="127.0.0.1", port=8000: FakeServer())
    monkeypatch.setattr(cli, "run_mcp_server", lambda server, *, transport="stdio", mount_path=None: None)

    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 0
    assert captured == {"log_level": None, "log_format": None}


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
        ["serve", "--transport", "sse", "--host", "0.0.0.0", "--port", "9000", "--mount-path", "/events"],
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
        ["run", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"],
    )

    assert result.exit_code == 0
    assert captured == {
        "transport": "streamable-http",
        "host": "0.0.0.0",
        "port": 9000,
        "mount_path": None,
    }


def test_version_flag_reports_package_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "0.1.0" in result.output


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
    assert "limits: 3 field(s)" in text.output
    assert json_result.exit_code == 0
    assert cast(AccessPayload, json.loads(json_result.output)) == payload


def test_access_reports_auth_error_as_json_stderr(monkeypatch: MonkeyPatch) -> None:
    def fake_access() -> object:
        raise EnjiApiError("AUTH_REQUIRED", "auth file does not exist")

    monkeypatch.setattr(cli, "get_access", fake_access)

    result = CliRunner().invoke(app, ["access"])

    assert result.exit_code == 3
    assert result.stderr == "AUTH_REQUIRED: auth file does not exist\n"


def test_report_list_passes_selector_and_json_output(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object | None] = {}
    payload: ReportsListPayload = {
        "projects": [{"id": "project_1", "name": "Pets", "repo_ids": ["repo_1"], "scores": {}}],
    }

    def fake_reports_list(selector: str = "*") -> ReportsListPayload:
        captured["selector"] = selector
        return payload

    monkeypatch.setattr(cli, "get_reports_list", fake_reports_list)

    result = CliRunner().invoke(
        app,
        ["report", "list", "--selector", "pets/*", "--json"],
    )

    assert result.exit_code == 0
    assert cast(ReportsListPayload, json.loads(result.output)) == payload
    assert captured == {"selector": "pets/*"}


def test_report_list_uses_expected_defaults(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object | None] = {}

    def fake_reports_list(selector: str = "*") -> ReportsListPayload:
        captured["selector"] = selector
        return {"projects": []}

    monkeypatch.setattr(cli, "get_reports_list", fake_reports_list)

    result = CliRunner().invoke(app, ["report", "list"])

    assert result.exit_code == 0
    assert captured == {"selector": "*"}


def test_report_list_reports_bad_selector_as_exit_code_four(monkeypatch: MonkeyPatch) -> None:
    def fake_reports_list(selector: str = "*") -> object:
        raise EnjiApiError("BAD_SELECTOR", f"bad selector: {selector}")

    monkeypatch.setattr(cli, "get_reports_list", fake_reports_list)

    result = CliRunner().invoke(app, ["report", "list", "--selector", "unknown"])

    assert result.exit_code == 4
    assert result.stderr == "BAD_SELECTOR: bad selector: unknown\n"


def test_project_list_routes_to_core_facade(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "list_projects", lambda: {"projects": [{"id": "project_1"}]})

    result = CliRunner().invoke(app, ["project", "list", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"projects": [{"id": "project_1"}]}


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

    result = CliRunner().invoke(app, ["--project", "Pets", "repo", "list", "--sort", "weakest"])

    assert result.exit_code == 0
    assert "project  repo" in result.output
    assert "Pets     j2h4u/enji-guard-cli" in result.output
    assert "tech-health=49" in result.output
    assert captured == {"project": "Pets", "sort": "weakest"}


def test_repo_list_can_emit_json(monkeypatch: MonkeyPatch) -> None:
    payload = {"projects": []}
    monkeypatch.setattr(cli, "list_project_inventory", lambda project, sort="default": payload)

    result = CliRunner().invoke(app, ["repo", "list", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == payload


def test_repo_resolve_defaults_to_current_repo_selector(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_resolve(repo: str | None, project: str | None) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        return {"resolved": True, "matches": [{"repo_id": "repo_1"}]}

    monkeypatch.setattr(cli, "resolve_repo", fake_resolve)

    result = CliRunner().invoke(app, ["repo", "resolve", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["resolved"] is True
    assert captured == {"repo": None, "project": None}


def test_repo_connect_uses_global_project_filter(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_connect(github_repo: str, project: str | None) -> dict[str, object]:
        captured["github_repo"] = github_repo
        captured["project"] = project
        return {"repo": {"id": "repo_1"}}

    monkeypatch.setattr(cli, "connect_repo", fake_connect)

    result = CliRunner().invoke(app, ["--project", "Pets", "repo", "connect", "j2h4u/enji-guard-cli", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"repo": {"id": "repo_1"}}
    assert captured == {"github_repo": "j2h4u/enji-guard-cli", "project": "Pets"}


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
                            "reports": {"ready": ["security"], "running": [], "missing": ["tests"], "reports": []},
                            "active_run_count": 0,
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
    assert "1 ready, 1 missing" in result.output
    assert "0307f239" in result.output
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
        return {"runs": []}

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
        return {"runs": []}

    monkeypatch.setattr(cli, "start_report_audits", fake_start)

    result = CliRunner().invoke(app, ["audit", "start", "j2h4u/enji-guard-cli", "--all", "--json"])

    assert result.exit_code == 0
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "project": None,
        "audits": [],
        "all_reports": True,
    }


def test_wait_routes_to_top_level_workflow(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_wait(
        repo: str,
        audit: AuditAlias,
        project: str | None,
        *,
        poll_seconds: int,
        timeout_seconds: int,
    ) -> dict[str, object]:
        captured["repo"] = repo
        captured["audit"] = audit.value
        captured["project"] = project
        captured["poll_seconds"] = poll_seconds
        captured["timeout_seconds"] = timeout_seconds
        return {"idle": True, "active_runs": []}

    monkeypatch.setattr(cli, "wait_for_work", fake_wait)

    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "wait", "j2h4u/enji-guard-cli", "recon", "--timeout-seconds", "30", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["idle"] is True
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "audit": "recon",
        "project": "Pets",
        "poll_seconds": 10,
        "timeout_seconds": 30,
    }


def test_wait_exits_two_when_timeout_payload_is_not_idle(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "wait_for_work",
        lambda repo, audit, project, *, poll_seconds, timeout_seconds: {
            "idle": False,
            "active_runs": [{"id": "task_1"}],
        },
    )

    result = CliRunner().invoke(app, ["wait", "repo_1", "security", "--timeout-seconds", "1", "--json"])

    assert result.exit_code == 2
    assert json.loads(result.output)["idle"] is False


def test_report_show_resolves_repo_selector_and_can_emit_markdown(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_show_report(repo: str, audit: AuditAlias, project: str | None) -> dict[str, object]:
        captured["repo"] = repo
        captured["audit"] = audit.value
        captured["project"] = project
        return {"snapshot": {"content": {"report": "# Security\n"}}}

    monkeypatch.setattr(cli, "show_report_for_repo", fake_show_report)

    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "report", "show", "j2h4u/enji-guard-cli", "security"],
    )

    assert result.exit_code == 0
    assert result.output == "# Security\n\n"
    assert captured == {"repo": "j2h4u/enji-guard-cli", "audit": "security", "project": "Pets"}


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
        return {"reports": [{"audit": "security"}]}

    monkeypatch.setattr(cli, "read_reports_for_repo", fake_read_reports)

    result = CliRunner().invoke(
        app,
        ["report", "read", "j2h4u/enji-guard-cli", "--all", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"reports": [{"audit": "security"}]}
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "project": None,
        "audits": [],
        "all_reports": True,
    }


def test_schedule_list_resolves_repo_selector(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_list(repo: str, project: str | None) -> dict[str, object]:
        captured["repo"] = repo
        captured["project"] = project
        return {"jobs": []}

    monkeypatch.setattr(cli, "list_schedules_for_repo", fake_list)

    result = CliRunner().invoke(app, ["--project", "Pets", "schedule", "list", "j2h4u/enji-guard-cli", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"jobs": []}
    assert captured == {"repo": "j2h4u/enji-guard-cli", "project": "Pets"}


def test_schedule_set_builds_typed_full_payload(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_set(repo: str, audit: AuditAlias, project: str | None, payload: object) -> dict[str, object]:
        captured["repo"] = repo
        captured["audit"] = audit.value
        captured["project"] = project
        captured["payload"] = payload
        return {"job": {"enabled": True}}

    monkeypatch.setattr(cli, "set_schedule_for_repo", fake_set)

    result = CliRunner().invoke(
        app,
        [
            "--project",
            "Pets",
            "schedule",
            "set",
            "j2h4u/enji-guard-cli",
            "security",
            "--freq",
            "weekly",
            "--day",
            "mon",
            "--at",
            "09:30@Asia/Almaty",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"job": {"enabled": True}}
    assert captured == {
        "repo": "j2h4u/enji-guard-cli",
        "audit": "security",
        "project": "Pets",
        "payload": {
            "enabled": True,
            "autoFix": False,
            "autofixVariantKey": "default",
            "frequency": "weekly",
            "daysOfWeek": ["mon"],
            "scheduleTimeSource": "user",
            "scheduleTime": "09:30",
            "timezone": "Asia/Almaty",
        },
    }


def test_schedule_disable_resolves_repo_selector(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_disable(repo: str, audit: AuditAlias, project: str | None) -> dict[str, object]:
        captured["repo"] = repo
        captured["audit"] = audit.value
        captured["project"] = project
        return {"job": {"enabled": False}}

    monkeypatch.setattr(cli, "disable_schedule_for_repo", fake_disable)

    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "schedule", "disable", "j2h4u/enji-guard-cli", "security", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"job": {"enabled": False}}
    assert captured == {"repo": "j2h4u/enji-guard-cli", "audit": "security", "project": "Pets"}


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
