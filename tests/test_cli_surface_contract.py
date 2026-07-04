from collections.abc import Iterable
from pathlib import Path

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

PROJECT_ADMIN_COMMANDS = (
    "enji-guard project create NAME",
    "enji-guard project rename PROJECT NAME",
    "enji-guard project delete PROJECT",
    "enji-guard repo remove REPO",
    "enji-guard repo move REPO --to-project PROJECT",
)

README_PROJECT_ADMIN_EXAMPLES = (
    "docker exec -i enji-guard-cli enji-guard project create Pets",
    "docker exec -i enji-guard-cli enji-guard project rename Pets Friends",
    "docker exec -i enji-guard-cli enji-guard project delete Pets",
    "docker exec -i enji-guard-cli enji-guard repo move j2h4u/enji-guard-cli --to-project Friends",
)

PROJECT_ADMIN_RULES = (
    "project delete succeeds only for empty projects",
    "repo move uses global --project as source project or selector disambiguation when needed",
)

DOC_MODEL_PHRASES = (
    "wait is the completion check after status",
    "Starting a new audit can temporarily hide older snapshots behind the running state",
    "Application telemetry is written only to the persistent file outside the container",
)


def test_visible_cli_command_inventory_matches_workflow_surface() -> None:
    command = get_command(app)

    assert set(_visible_command_paths(command)) == EXPECTED_VISIBLE_COMMANDS
    assert REMOVED_VISIBLE_COMMANDS.isdisjoint(set(_visible_command_paths(command)))


def test_visible_cli_surface_excludes_raw_api_plumbing() -> None:
    command_names = {part for path in _visible_command_paths(get_command(app)) for part in path}

    assert command_names.isdisjoint(RAW_PLUMBING_NAMES)


def test_documented_cli_surface_covers_project_admin_and_repo_move_commands() -> None:
    readme = _normalized_text(Path("README.md"))
    design = _normalized_text(Path("docs/cli-surface-design.md"))
    spec = _normalized_text(Path("docs/enji-cli-mcp-spec.md"))

    for command in PROJECT_ADMIN_COMMANDS:
        assert command in design
        assert command in spec

    for example in README_PROJECT_ADMIN_EXAMPLES:
        assert example in readme

    for rule in PROJECT_ADMIN_RULES:
        assert rule in readme
        assert rule in design
        assert rule in spec

    for phrase in DOC_MODEL_PHRASES:
        assert phrase in readme or phrase in spec


def _visible_command_paths(command: object, prefix: tuple[str, ...] = ()) -> Iterable[tuple[str, ...]]:
    child_commands = getattr(command, "commands", None)
    if not isinstance(child_commands, dict):
        yield prefix
        return
    for name, child in child_commands.items():
        if not isinstance(name, str) or getattr(child, "hidden", False):
            continue
        yield from _visible_command_paths(child, (*prefix, name))


def _normalized_text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").replace("`", "").split())
