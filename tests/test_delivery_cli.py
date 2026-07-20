import importlib
from collections.abc import Callable
from typing import cast

import pytest
import typer
from typer.testing import CliRunner

from enji_guard_cli.application import (
    Application,
    ApplicationAuthError,
    ApplicationCommandError,
    ApplicationResult,
    AutofixWriteScope,
)
from enji_guard_cli.audit.ports import AuditGatewayPort
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.delivery.cli.app import _command_exit_code, _run, app
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")


def test_operator_command_tree_uses_audit_vocabulary() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("auth", "project", "repo", "recon", "audit", "schedule", "improvement-jobs", "email", "language"):
        assert command in result.stdout


def test_audit_read_and_summary_are_public_commands() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["audit", "read", "--help"]).exit_code == 0
    assert runner.invoke(app, ["audit", "summary", "--help"]).exit_code == 0


def test_run_defaults_to_long_lived_http_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_service(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "run_service", fake_run_service)

    result = CliRunner().invoke(app, ["run", "--port", "18080"])

    assert result.exit_code == 0
    assert captured["transport"] == "streamable-http"
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 18080


def test_run_keeps_stdio_as_an_explicit_interactive_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_service(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "run_service", fake_run_service)

    result = CliRunner().invoke(app, ["run", "--transport", "stdio"])

    assert result.exit_code == 0
    assert captured["transport"] == "stdio"


class _FakeAuth:
    def status(self) -> dict[str, object]:
        return {"authenticated": True, "credential_type": "bearer"}

    def refresh(self) -> dict[str, object]:
        return {"ok": True, "credential_type": "cookie"}

    def import_cookie(self, value: str) -> dict[str, object]:
        return {"ok": True, "credential_type": "cookie", "length": len(value)}

    def import_bearer_token(self, value: str) -> dict[str, object]:
        return {"ok": True, "credential_type": "bearer", "length": len(value)}


class _FakeApplication:
    def __init__(self) -> None:
        self.auth = _FakeAuth()
        self.calls: list[tuple[str, object]] = []

    def execute(self, action: Callable[[], object]) -> ApplicationResult:
        return ApplicationResult(action())

    def project_settings(self, project: str | None) -> object:
        self.calls.append(("project_settings", project))
        return {"project": project, "repositories": []}

    def import_bearer(self, value: str) -> dict[str, object]:
        return self.auth.import_bearer_token(value)

    def access(self) -> object:
        self.calls.append(("access", None))
        return {"full_access": True}

    def audit_start(self, repo: str, project: str | None, selectors: list[str], *, all_audits: bool) -> object:
        self.calls.append(("audit_start", (repo, project, selectors, all_audits)))
        return {"repo_id": repo, "project_id": project, "results": [{"state": "started"}]}

    def set_schedules(self, repo: str | None, project: str | None, **options: object) -> object:
        self.calls.append(("set_schedules", (repo, project, options)))
        return [{"state": "unchanged"}]

    def set_email_preferences(self, repo: str | None, project: str | None, update: object, *, scope: object) -> object:
        self.calls.append(("set_email_preferences", (repo, project, update, scope)))
        return [{"state": "changed"}]

    def set_autofixes(self, *args: object, **options: object) -> object:
        self.calls.append(("set_autofixes", (*args, options)))
        return [{"state": "unchanged"}]


def test_audit_start_calls_typed_application_and_emits_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    result = CliRunner().invoke(app, ["audit", "start", "org/repo", "security", "--project", "Pets", "--json"])
    assert result.exit_code == 0
    assert result.exception is None
    assert '"repo_id": "org/repo"' in result.stdout
    assert fake.calls == [("audit_start", ("org/repo", "Pets", ["security"], False))]


def test_project_settings_and_access_use_typed_application_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    settings = CliRunner().invoke(app, ["project", "settings", "--project", "Pets", "--json"])
    access = CliRunner().invoke(app, ["access", "--json"])
    assert settings.exit_code == 0
    assert access.exit_code == 0
    assert fake.calls[:2] == [("project_settings", "Pets"), ("access", None)]


def test_batch_write_options_are_forwarded_with_explicit_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "schedule", "set", "--all-repos", "--enabled", "on", "--frequency", "daily"],
    )
    assert result.exit_code == 0
    name, args = fake.calls[-1]
    assert name == "set_schedules"
    values = cast(tuple[object, object, dict[str, object]], args)
    assert values[0:2] == (None, "Pets")
    assert cast(AutofixWriteScope, values[2]["scope"]).all_repos is True


def test_autofix_write_options_are_forwarded_with_canonical_keyword_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

    result = CliRunner().invoke(
        app,
        [
            "--project",
            "Pets",
            "improvement-jobs",
            "set",
            "tests/test-writing",
            "--all-repos",
            "--enabled",
            "on",
            "--frequency",
            "weekly",
            "--timezone",
            "Asia/Almaty",
        ],
    )

    assert result.exit_code == 0
    name, args = fake.calls[-1]
    assert name == "set_autofixes"
    values = cast(tuple[object, object, object, dict[str, object]], args)
    assert values[:3] == (None, "Pets", ["tests/test-writing"])
    assert values[3]["enabled"] is True
    assert values[3]["cadence"] == "weekly"
    assert values[3]["timezone"] == "Asia/Almaty"
    assert cast(AutofixWriteScope, values[3]["scope"]).all_repos is True


def test_batch_write_rejects_ambiguous_scope_before_application(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    result = CliRunner().invoke(app, ["email", "set", "--all-repos", "--all-projects", "--manual", "on"])
    assert result.exit_code == 1
    assert "pass --all-repos or --all-projects" in result.stderr
    assert fake.calls == []


def test_auth_import_bearer_requires_stdin_and_never_prints_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    missing = CliRunner().invoke(app, ["auth", "import-bearer"])
    assert missing.exit_code == 1
    assert "use --stdin" in missing.stderr
    result = CliRunner().invoke(app, ["auth", "import-bearer", "--stdin", "--json"], input="Bearer secret-token\n")
    assert result.exit_code == 0
    assert "secret-token" not in result.stdout


@pytest.mark.parametrize(
    ("error", "exit_code", "rendered"),
    [
        (EnjiApiError("BAD_SELECTOR", "unknown audit"), 4, "BAD_SELECTOR: unknown audit"),
        (ApplicationAuthError("AUTH_EXPIRED", "authentication expired"), 3, "AUTH_EXPIRED: authentication expired"),
        (OSError("disk full"), 1, "STORAGE: disk full"),
        (ValueError("invalid audit scope"), 1, "VALIDATION: invalid audit scope"),
    ],
)
def test_run_maps_current_application_errors_to_cli_contract(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    exit_code: int,
    rendered: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = Application(
        audit_gateway=cast(AuditGatewayPort, object()),
        portfolio_gateway=cast(PortfolioGatewayPort, object()),
        auth=cast(AuthSessionService, object()),
    )
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: application)

    def fail() -> object:
        raise error

    with pytest.raises(typer.Exit) as caught:
        _run(fail, False)

    assert caught.value.exit_code == exit_code
    assert rendered in capsys.readouterr().err


def test_journey_telemetry_uses_application_command_exit_code() -> None:
    assert _command_exit_code(ApplicationCommandError("AUTH_EXPIRED", "expired", 3)) == 3
    assert _command_exit_code(ValueError("invalid")) == 1
