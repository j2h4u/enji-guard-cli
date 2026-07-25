"""Audit selectors and --all must never be silently reconciled behind the operator."""

import importlib

import pytest
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from application_builder import FacadeRouter
from enji_guard_cli.application import ApplicationResult
from enji_guard_cli.audit.artifacts import AuditSummary
from enji_guard_cli.delivery.cli.app import app

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")


class _RecordingApplication:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def execute(self, action: object) -> ApplicationResult:
        assert callable(action)
        return ApplicationResult(action())

    def _record(self, name: str, *args: object, **kwargs: object) -> object:
        self.calls.append((name, args, kwargs))
        return {"state": "recorded"}

    def audit_start(self, *args: object, **kwargs: object) -> object:
        return self._record("audit_start", *args, **kwargs)

    def audit_read(self, *args: object, **kwargs: object) -> object:
        return self._record("audit_read", *args, **kwargs)

    def audit_summary(self, *args: object, **kwargs: object) -> AuditSummary:
        self._record("audit_summary", *args, **kwargs)
        return AuditSummary("r1", ())


@pytest.fixture
def application(monkeypatch: pytest.MonkeyPatch) -> _RecordingApplication:
    fake = _RecordingApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: FacadeRouter(fake))
    return fake


@pytest.mark.parametrize("command", ["start", "read"])
def test_all_with_explicit_selectors_is_rejected_before_any_call(
    application: _RecordingApplication, command: str
) -> None:
    result = CliRunner().invoke(app, ["audit", command, "github@github.com:owner/name", "security", "--all"])

    assert result.exit_code == 1
    assert "pass audit selectors or --all, not both" in result.stderr
    assert application.calls == []


def test_audit_start_still_accepts_all_alone(application: _RecordingApplication) -> None:
    result = CliRunner().invoke(app, ["audit", "start", "github@github.com:owner/name", "--all"])

    assert result.exit_code == 0
    name, args, kwargs = application.calls[-1]
    assert name == "audit_start"
    assert args[2] == []
    assert kwargs["all_audits"] is True


def test_audit_start_still_accepts_selectors_alone(application: _RecordingApplication) -> None:
    result = CliRunner().invoke(app, ["audit", "start", "github@github.com:owner/name", "audit.security"])

    assert result.exit_code == 0
    name, args, kwargs = application.calls[-1]
    assert name == "audit_start"
    assert args[2] == ["security"]
    assert kwargs["all_audits"] is False


def test_audit_summary_has_no_all_flag() -> None:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    audit = root.commands["audit"]
    assert isinstance(audit, TyperGroup)
    summary = audit.commands["summary"]
    assert "--all" not in {option for param in summary.params for option in param.opts}

    result = CliRunner().invoke(app, ["audit", "summary", "github@github.com:owner/name", "--all"])

    assert result.exit_code == 2


def test_audit_summary_without_selectors_summarizes_everything(application: _RecordingApplication) -> None:
    result = CliRunner().invoke(app, ["audit", "summary", "github@github.com:owner/name"])

    assert result.exit_code == 0
    name, args, _kwargs = application.calls[-1]
    assert name == "audit_summary"
    assert args[1] == []
