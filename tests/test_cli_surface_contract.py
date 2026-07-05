from collections.abc import Iterable

from typer.main import get_command

from enji_guard_cli.cli import app

EXPECTED_VISIBLE_COMMANDS = {
    ("access",),
    ("auth", "import-cookie"),
    ("auth", "import-token"),
    ("auth", "refresh"),
    ("auth", "status"),
    ("audit", "start"),
    ("email", "list"),
    ("email", "set"),
    ("health",),
    ("project", "create"),
    ("project", "delete"),
    ("project", "list"),
    ("project", "rename"),
    ("recon", "start"),
    ("repo", "add"),
    ("repo", "list"),
    ("repo", "move"),
    ("repo", "remove"),
    ("repo", "resolve"),
    ("report", "read"),
    ("report", "summary"),
    ("run",),
    ("schedule", "list"),
    ("schedule", "auto-time"),
    ("schedule", "set"),
    ("status",),
    ("wait",),
}

REMOVED_VISIBLE_COMMANDS = {
    ("report", "list"),
    ("report", "show"),
    ("repo", "connect"),
    ("schedule", "timezone"),
}

RAW_PLUMBING_NAMES = {
    "active-runs",
    "audit-history",
    "audit-rerun-state",
    "github-installations",
    "history",
    "runbook",
    "task-link",
    "task-links",
    "transfer",
    "preflight",
}


def test_visible_cli_command_inventory_matches_workflow_surface() -> None:
    command = get_command(app)

    assert set(_visible_command_paths(command)) == EXPECTED_VISIBLE_COMMANDS
    assert REMOVED_VISIBLE_COMMANDS.isdisjoint(set(_visible_command_paths(command)))


def test_visible_cli_surface_excludes_raw_api_plumbing() -> None:
    command_names = {part for path in _visible_command_paths(get_command(app)) for part in path}

    assert command_names.isdisjoint(RAW_PLUMBING_NAMES)


def _visible_command_paths(command: object, prefix: tuple[str, ...] = ()) -> Iterable[tuple[str, ...]]:
    child_commands = getattr(command, "commands", None)
    if not isinstance(child_commands, dict):
        yield prefix
        return
    for name, child in child_commands.items():
        if not isinstance(name, str) or getattr(child, "hidden", False):
            continue
        yield from _visible_command_paths(child, (*prefix, name))
