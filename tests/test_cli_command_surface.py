"""The README must document the whole command surface, and duplicates stay deleted."""

from pathlib import Path

import pytest
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from enji_guard_cli.delivery.cli.app import app

README = Path(__file__).resolve().parent.parent / "README.md"


def _surface() -> list[tuple[str, ...]]:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    commands: list[tuple[str, ...]] = []
    for name, command in root.commands.items():
        if isinstance(command, TyperGroup):
            commands.extend((name, child) for child in command.commands)
        else:
            commands.append((name,))
    return commands


def _documented_surface() -> str:
    readme = README.read_text(encoding="utf-8")
    section = readme.split("### Full Command Surface", 1)
    assert len(section) == 2, "README must document the full command surface"
    return section[1].split("### Exit Codes", 1)[0]


@pytest.mark.parametrize("command", _surface())
def test_every_command_is_documented(command: tuple[str, ...]) -> None:
    documented = _documented_surface()
    for part in command:
        assert part in documented, f"{' '.join(command)} is missing from the README command surface"


@pytest.mark.parametrize(
    "command",
    [
        ["repo", "status"],
        ["recon", "status"],
        ["audit", "status"],
        ["audit", "wait"],
        ["portfolio", "status"],
        ["repo", "list"],
    ],
)
def test_removed_duplicate_commands_stay_removed(command: list[str]) -> None:
    assert tuple(command) not in _surface()
    assert CliRunner().invoke(app, [*command, "github@github.com:owner/name"]).exit_code == 2


def test_exactly_one_repository_snapshot_command() -> None:
    """``status`` is the only snapshot; ``auth status`` reports credentials, not audits."""
    snapshots = [command for command in _surface() if command[-1] == "status"]

    assert sorted(snapshots) == [("auth", "status"), ("status",)]


def test_exactly_one_blocking_wait_command() -> None:
    assert [command for command in _surface() if command[-1] == "wait"] == [("wait",)]
