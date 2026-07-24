"""Batch write commands must never accept an implicit repository scope."""

import importlib

import pytest
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from enji_guard_cli.application import ApplicationResult
from enji_guard_cli.delivery.cli.app import app

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

BATCH_WRITE_COMMANDS = [
    (["schedule", "set", "--enabled", "on"], "set_schedules"),
    (["schedule", "auto-time"], "schedule_auto_time"),
    (["schedule", "timezone", "Asia/Almaty"], "set_schedules"),
    (["improvement-jobs", "set", "--all", "--enabled", "on"], "set_autofixes"),
    (["email", "set", "--manual", "on"], "set_email_preferences"),
]


class _RecordingApplication:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, action: object) -> ApplicationResult:
        assert callable(action)
        return ApplicationResult(action())

    def _record(self, name: str, *args: object) -> tuple[object, ...]:
        self.calls.append((name, args))
        return ()

    def set_schedules(self, *args: object, **_kwargs: object) -> tuple[object, ...]:
        return self._record("set_schedules", *args)

    def schedule_auto_time(self, *args: object, **_kwargs: object) -> tuple[object, ...]:
        return self._record("schedule_auto_time", *args)

    def set_autofixes(self, *args: object, **_kwargs: object) -> tuple[object, ...]:
        return self._record("set_autofixes", *args)

    def set_email_preferences(self, *args: object, **_kwargs: object) -> tuple[object, ...]:
        return self._record("set_email_preferences", *args)


@pytest.fixture
def application(monkeypatch: pytest.MonkeyPatch) -> _RecordingApplication:
    fake = _RecordingApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    return fake


@pytest.mark.parametrize(("command", "_method"), BATCH_WRITE_COMMANDS)
def test_batch_write_without_any_scope_is_refused(
    application: _RecordingApplication, command: list[str], _method: str
) -> None:
    result = CliRunner().invoke(app, command)

    assert result.exit_code == 1
    assert "pass --repo REPO, --all-repos with --project, or --all-projects" in result.stderr
    assert application.calls == []


@pytest.mark.parametrize(("command", "method"), BATCH_WRITE_COMMANDS)
def test_batch_write_accepts_a_named_repo_scope(
    application: _RecordingApplication, command: list[str], method: str
) -> None:
    result = CliRunner().invoke(app, [*command, "--repo", "github@github.com:owner/name"])

    assert result.exit_code == 0
    name, args = application.calls[-1]
    assert name == method
    assert args[0] == "github@github.com:owner/name"


def test_documented_improvement_jobs_example_targets_the_named_repository(
    application: _RecordingApplication,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "improvement-jobs",
            "set",
            "--repo",
            "github@github.com:j2h4u/enji-guard-cli",
            "security",
            "vuln-fix",
            "--enabled",
            "on",
        ],
    )

    assert result.exit_code == 0
    name, args = application.calls[-1]
    assert name == "set_autofixes"
    assert args[0] == "github@github.com:j2h4u/enji-guard-cli"
    assert args[2] == ["security", "vuln-fix"]


def test_repo_selector_is_never_swallowed_as_an_autofix_selector(
    application: _RecordingApplication,
) -> None:
    result = CliRunner().invoke(
        app,
        ["improvement-jobs", "set", "github@github.com:j2h4u/enji-guard-cli", "security", "--enabled", "on"],
    )

    assert result.exit_code == 1
    assert "pass --repo REPO" in result.stderr
    assert application.calls == []


@pytest.mark.parametrize(
    ("group", "command"),
    [
        ("schedule", "list"),
        ("schedule", "set"),
        ("improvement-jobs", "list"),
        ("improvement-jobs", "set"),
        ("email", "list"),
        ("email", "set"),
    ],
)
def test_batch_commands_document_their_repo_option(group: str, command: str) -> None:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    group_command = root.commands[group]
    assert isinstance(group_command, TyperGroup)
    params = {option: param for param in group_command.commands[command].params for option in param.opts}
    assert params["--repo"].help


@pytest.mark.parametrize("command", [["status"], ["audit", "start"], ["audit", "status"], ["recon", "start"]])
def test_single_repository_commands_keep_repo_positional(command: list[str]) -> None:
    result = CliRunner().invoke(app, [*command, "--help"])

    assert result.exit_code == 0
    assert "[OPTIONS]" in result.stdout
    assert "--repo" not in result.stdout


def test_batch_write_scope_stays_mutually_exclusive(application: _RecordingApplication) -> None:
    result = CliRunner().invoke(
        app, ["email", "set", "--manual", "on", "--repo", "github@github.com:owner/name", "--all-repos"]
    )

    assert result.exit_code == 1
    assert "--repo cannot be combined with --all-repos or --all-projects" in result.stderr
    assert application.calls == []
