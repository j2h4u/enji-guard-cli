import json
from pathlib import Path
from typing import TypedDict, cast

from pytest import MonkeyPatch
from typer.testing import CliRunner

from enji_guard_cli import cli
from enji_guard_cli.cli import app
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


def test_version_flag_reports_package_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_catalog_audits_reports_canonical_identifier_map() -> None:
    result = CliRunner().invoke(app, ["catalog", "audits"])

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
    result = CliRunner().invoke(app, ["catalog", "audit", "deps"])

    assert result.exit_code == 0
    audit = cast(AuditPayload, json.loads(result.output))
    assert audit["alias"] == "deps"
    assert audit["route_slug"] == "dependency-hygiene"
    assert audit["job_kind"] == "dependency-hygiene"
    assert audit["action_key"] == "audit.dependency-hygiene"


def test_access_reports_json_and_pretty_output(monkeypatch: MonkeyPatch) -> None:
    payload: AccessPayload = {
        "group": "pro",
        "full_access": True,
        "limits": {"can_use_schedules": True, "audit_runs": {}, "autofix_runs": {}},
        "usage": [],
    }

    monkeypatch.setattr(cli, "get_access", lambda: payload)

    compact = CliRunner().invoke(app, ["access"])
    pretty = CliRunner().invoke(app, ["access", "--pretty"])

    assert compact.exit_code == 0
    assert cast(AccessPayload, json.loads(compact.output)) == payload
    assert pretty.exit_code == 0
    assert pretty.output.startswith("{\n  ")


def test_access_reports_auth_error_as_json_stderr(monkeypatch: MonkeyPatch) -> None:
    def fake_access() -> object:
        raise EnjiApiError("AUTH_REQUIRED", "auth file does not exist")

    monkeypatch.setattr(cli, "get_access", fake_access)

    result = CliRunner().invoke(app, ["access"])

    assert result.exit_code == 3
    assert json.loads(result.stderr) == {"code": "AUTH_REQUIRED", "message": "auth file does not exist"}


def test_report_list_passes_selector_and_pretty_output(monkeypatch: MonkeyPatch) -> None:
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
        ["report", "list", "--selector", "pets/*", "--pretty"],
    )

    assert result.exit_code == 0
    assert result.output.startswith("{\n  ")
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
    assert json.loads(result.stderr) == {"code": "BAD_SELECTOR", "message": "bad selector: unknown"}


def test_auth_status_reports_json_and_zero_exit_code(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
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
    payload = cast(AuthStatusPayload, json.loads(result.output))
    assert payload["authenticated"] is True
    assert payload["credential_type"] == "bearer_token"
    assert payload["email"] == "user@example.com"


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
    payload = cast(AuthStatusPayload, json.loads(result.output))
    assert payload["authenticated"] is False
    assert payload["code"] == "AUTH_REQUIRED"


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

    result = CliRunner().invoke(app, ["auth", "refresh", "--auth-file", str(auth_file), "--pretty"])

    assert result.exit_code == 0
    assert result.output.startswith("{\n  ")
    assert json.loads(result.output) == payload


def test_auth_status_awaits_async_result_for_pretty_json(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
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

    result = CliRunner().invoke(app, ["auth", "status", "--auth-file", str(auth_file), "--pretty"])

    assert result.exit_code == 0
    assert result.output.startswith("{\n  ")
    payload = cast(AuthStatusPayload, json.loads(result.output))
    assert payload["authenticated"] is True
    assert payload["credential_type"] == "cookie"
