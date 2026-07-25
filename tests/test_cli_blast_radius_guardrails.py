"""Irreversible writes must be confirmed: --all-projects, project delete, repo remove."""

import importlib

import pytest
import typer
from typer.testing import CliRunner

from application_builder import FacadeRouter
from enji_guard_cli.application import ApplicationResult, AutofixWriteScope
from enji_guard_cli.delivery.cli.app import app

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

BATCH_WRITES = [
    ["schedule", "set", "--enabled", "on"],
    ["schedule", "auto-time"],
    ["schedule", "timezone", "Asia/Almaty"],
    ["improvement-jobs", "set", "--all", "--enabled", "on"],
    ["email", "set", "--manual", "on"],
]


class _RecordingApplication:
    def __init__(self) -> None:
        self.scopes: list[AutofixWriteScope] = []
        self.deletions: list[tuple[str, str]] = []

    def execute(self, action: object) -> ApplicationResult:
        assert callable(action)
        return ApplicationResult(action())

    def _record(self, **kwargs: object) -> tuple[object, ...]:
        scope = kwargs["scope"]
        assert isinstance(scope, AutofixWriteScope)
        self.scopes.append(scope)
        return ()

    def set_schedules(self, *_args: object, **kwargs: object) -> tuple[object, ...]:
        return self._record(**kwargs)

    def schedule_auto_time(self, *_args: object, **kwargs: object) -> tuple[object, ...]:
        return self._record(**kwargs)

    def set_autofixes(self, *_args: object, **kwargs: object) -> tuple[object, ...]:
        return self._record(**kwargs)

    def set_email_preferences(self, *_args: object, **kwargs: object) -> tuple[object, ...]:
        return self._record(**kwargs)

    def delete_project(self, project: str) -> tuple[object, ...]:
        self.deletions.append(("delete_project", project))
        return ()

    def remove_repository(self, repo: str, _project: str | None = None) -> tuple[object, ...]:
        self.deletions.append(("remove_repository", repo))
        return ()


@pytest.fixture
def application(monkeypatch: pytest.MonkeyPatch) -> _RecordingApplication:
    fake = _RecordingApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: FacadeRouter(fake))
    return fake


@pytest.mark.parametrize("command", BATCH_WRITES)
def test_all_projects_without_confirmation_is_refused(application: _RecordingApplication, command: list[str]) -> None:
    result = CliRunner().invoke(app, [*command, "--all-projects"])

    assert result.exit_code == 1
    assert "CONFIRMATION_REQUIRED" in result.stderr
    assert "--yes" in result.stderr
    assert application.scopes == []


@pytest.mark.parametrize("command", BATCH_WRITES)
def test_all_projects_with_yes_proceeds(application: _RecordingApplication, command: list[str]) -> None:
    result = CliRunner().invoke(app, [*command, "--all-projects", "--yes"])

    assert result.exit_code == 0
    assert application.scopes == [AutofixWriteScope(all_repos=False, all_projects=True)]


def test_json_mode_never_prompts_and_reports_a_json_envelope(application: _RecordingApplication) -> None:
    result = CliRunner().invoke(app, ["--json", "email", "set", "--manual", "on", "--all-projects"])

    assert result.exit_code == 1
    assert result.stderr.lstrip().startswith("{")
    assert "CONFIRMATION_REQUIRED" in result.stderr
    assert application.scopes == []


def test_bounded_scopes_stay_unconfirmed(application: _RecordingApplication) -> None:
    result = CliRunner().invoke(app, ["--project", "Pets", "email", "set", "--manual", "on", "--all-repos"])

    assert result.exit_code == 0
    assert application.scopes == [AutofixWriteScope(all_repos=True, all_projects=False)]


def test_interactive_operator_is_prompted_and_can_decline(
    application: _RecordingApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_module, "_is_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda _message: False)

    result = CliRunner().invoke(app, ["email", "set", "--manual", "on", "--all-projects"])

    assert result.exit_code == 1
    assert "ABORTED" in result.stderr
    assert application.scopes == []


def test_interactive_operator_can_accept(application: _RecordingApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "_is_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda _message: True)

    result = CliRunner().invoke(app, ["email", "set", "--manual", "on", "--all-projects"])

    assert result.exit_code == 0
    assert application.scopes == [AutofixWriteScope(all_repos=False, all_projects=True)]


DELETIONS = [
    (["project", "delete", "Pets"], ("delete_project", "Pets")),
    (["repo", "remove", "github@github.com:owner/name"], ("remove_repository", "github@github.com:owner/name")),
]


@pytest.mark.parametrize(("command", "expected"), DELETIONS)
def test_deletion_without_confirmation_is_refused(
    application: _RecordingApplication, command: list[str], expected: tuple[str, str]
) -> None:
    del expected
    result = CliRunner().invoke(app, command)

    assert result.exit_code == 1
    assert "CONFIRMATION_REQUIRED" in result.stderr
    assert "--yes" in result.stderr
    assert application.deletions == []


@pytest.mark.parametrize(("command", "expected"), DELETIONS)
def test_deletion_with_yes_proceeds(
    application: _RecordingApplication, command: list[str], expected: tuple[str, str]
) -> None:
    result = CliRunner().invoke(app, [*command, "--yes"])

    assert result.exit_code == 0
    assert application.deletions == [expected]


@pytest.mark.parametrize(("command", "expected"), DELETIONS)
def test_deletion_in_json_mode_never_prompts(
    application: _RecordingApplication, command: list[str], expected: tuple[str, str]
) -> None:
    del expected
    result = CliRunner().invoke(app, ["--json", *command])

    assert result.exit_code == 1
    assert result.stderr.lstrip().startswith("{")
    assert "CONFIRMATION_REQUIRED" in result.stderr
    assert application.deletions == []


@pytest.mark.parametrize(("command", "expected"), DELETIONS)
def test_interactive_operator_can_decline_a_deletion(
    application: _RecordingApplication,
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    expected: tuple[str, str],
) -> None:
    del expected
    monkeypatch.setattr(cli_module, "_is_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda _message: False)

    result = CliRunner().invoke(app, command)

    assert result.exit_code == 1
    assert "ABORTED" in result.stderr
    assert application.deletions == []


@pytest.mark.parametrize(("command", "expected"), DELETIONS)
def test_interactive_operator_can_accept_a_deletion(
    application: _RecordingApplication,
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    expected: tuple[str, str],
) -> None:
    monkeypatch.setattr(cli_module, "_is_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda _message: True)

    result = CliRunner().invoke(app, command)

    assert result.exit_code == 0
    assert application.deletions == [expected]
