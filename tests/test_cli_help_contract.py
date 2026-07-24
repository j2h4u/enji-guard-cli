"""Legal values must be discoverable from --help, not only from a failed run."""

import pytest
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from enji_guard_cli.application import AUDIT_SCHEDULE_FREQUENCIES
from enji_guard_cli.delivery.cli.app import _repository_sort, app
from enji_guard_cli.settings import REPOSITORY_SORT_NAMES


def _command(path: list[str]) -> object:
    command = get_command(app)
    for part in path:
        assert isinstance(command, TyperGroup)
        command = command.commands[part]
    return command


@pytest.mark.parametrize("name", ["health", "access", "run"])
def test_top_level_commands_have_help(name: str) -> None:
    command = _command([name])
    assert getattr(command, "help", None), f"{name} has no help text"

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0


def test_health_help_points_probes_at_the_ready_mode() -> None:
    result = CliRunner().invoke(app, ["health", "--help"])

    assert result.exit_code == 0
    rendered = " ".join(result.stdout.split())
    assert "--ready" in rendered
    assert "healthchecks" in rendered


@pytest.mark.parametrize("path", [["status"], ["portfolio", "status"], ["repo", "list"]])
def test_sort_help_lists_every_legal_value(path: list[str]) -> None:
    result = CliRunner().invoke(app, [*path, "--help"])

    assert result.exit_code == 0
    rendered = " ".join(result.stdout.split())
    for value in REPOSITORY_SORT_NAMES:
        assert value in rendered


def test_sort_rejection_message_matches_the_documented_values() -> None:
    result = CliRunner().invoke(app, ["status", "--sort", "nope"])

    assert result.exit_code == 2
    rendered = " ".join(result.stderr.replace("\u2502", " ").split())
    assert "sort must be one of" in rendered
    for value in REPOSITORY_SORT_NAMES:
        assert value in rendered


@pytest.mark.parametrize("value", sorted(REPOSITORY_SORT_NAMES))
def test_every_settings_sort_name_is_accepted(value: str) -> None:
    assert _repository_sort(value) == value


@pytest.mark.parametrize("path", [["schedule", "set"], ["improvement-jobs", "set"]])
def test_frequency_help_lists_every_legal_cadence(path: list[str]) -> None:
    result = CliRunner().invoke(app, [*path, "--help"])

    assert result.exit_code == 0
    rendered = " ".join(result.stdout.split())
    for cadence in AUDIT_SCHEDULE_FREQUENCIES:
        assert cadence in rendered
    assert "IANA timezone" in rendered
    assert "Turn the subscription on or off" in rendered
