"""Batch write commands must never accept an implicit repository scope.

Scope is asserted where it takes effect -- the expansion the subscriptions
facade asks ``PortfolioTargetService`` for -- so a facade that inverts
``--all-repos`` into ``--all-projects``, or downgrades a write to a read
``operation``, fails here rather than silently rewriting an account.
"""

import importlib

import pytest
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from application_builder import (
    PETS,
    RecordingAuditGateway,
    RecordingTargetService,
    WriteTargetsCall,
    recording_application,
    repository,
)
from cli_output import rendered as _rendered
from enji_guard_cli.audit.ports import AuditAutofixJob
from enji_guard_cli.delivery.cli.app import app

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

REPO_SELECTOR = "github@github.com:owner/name"

BATCH_WRITE_COMMANDS = [
    ["schedule", "set", "--enabled", "on"],
    ["schedule", "auto-time"],
    ["schedule", "timezone", "Asia/Almaty"],
    ["improvement-jobs", "set", "--all", "--enabled", "on"],
    ["email", "set", "--manual", "on"],
]

EXISTING_JOBS = (
    AuditAutofixJob("improvement.vuln-fix", "default", "vuln-fix", True, True, timezone="UTC"),
    AuditAutofixJob("improvement.test-writing", "default", "test-writing", True, True, timezone="UTC"),
)


@pytest.fixture
def targets(monkeypatch: pytest.MonkeyPatch) -> RecordingTargetService:
    """Install one CLI application whose write scope is observable."""
    service = RecordingTargetService((repository("owner/name"),))
    application = recording_application(
        audit=RecordingAuditGateway(autofix_jobs={"r1": EXISTING_JOBS}),
        targets=service,
    )
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: application)
    return service


@pytest.mark.parametrize("command", BATCH_WRITE_COMMANDS)
def test_batch_write_without_any_scope_is_refused(targets: RecordingTargetService, command: list[str]) -> None:
    result = CliRunner().invoke(app, command)

    assert result.exit_code == 1
    assert "pass --repo REPO, --all-repos with --project, or --all-projects" in result.stderr
    assert targets.write_targets_calls == []


@pytest.mark.parametrize("command", BATCH_WRITE_COMMANDS)
def test_batch_write_expands_exactly_the_named_repository_scope(
    targets: RecordingTargetService, command: list[str]
) -> None:
    result = CliRunner().invoke(app, [*command, "--repo", REPO_SELECTOR])

    assert result.exit_code == 0
    assert targets.write_targets_calls == [WriteTargetsCall(REPO_SELECTOR, None, False, False, "mutation")]


@pytest.mark.parametrize("command", BATCH_WRITE_COMMANDS)
def test_all_repos_expands_a_project_scope_and_never_an_account_scope(
    targets: RecordingTargetService, command: list[str]
) -> None:
    """``--all-repos`` must reach the port as a project-bounded expansion.

    Inverting the two scope flags is invisible in the command output but
    rewrites every project in the account, so the flags are asserted apart.
    """
    result = CliRunner().invoke(app, ["--project", "Pets", *command, "--all-repos"])

    assert result.exit_code == 0
    assert targets.write_targets_calls == [WriteTargetsCall(None, "Pets", True, False, "mutation")]


def test_documented_improvement_jobs_example_targets_the_named_repository(
    targets: RecordingTargetService,
) -> None:
    result = CliRunner().invoke(
        app,
        ["improvement-jobs", "set", "--repo", REPO_SELECTOR, "vuln-fix", "--enabled", "on", "--frequency", "weekly"],
    )

    assert result.exit_code == 0
    assert targets.write_targets_calls == [WriteTargetsCall(REPO_SELECTOR, None, False, False, "mutation")]


def test_repo_selector_is_never_swallowed_as_an_autofix_selector(
    targets: RecordingTargetService,
) -> None:
    result = CliRunner().invoke(app, ["improvement-jobs", "set", REPO_SELECTOR, "vuln-fix", "--enabled", "on"])

    assert result.exit_code == 1
    assert "pass --repo REPO" in result.stderr
    assert targets.write_targets_calls == []


def test_read_commands_never_expand_a_write_scope(targets: RecordingTargetService) -> None:
    """Listing is a read: it must resolve targets, not a mutation scope."""
    result = CliRunner().invoke(app, ["schedule", "list", "--repo", REPO_SELECTOR])

    assert result.exit_code == 0
    assert targets.write_targets_calls == []
    assert [call.repo for call in targets.read_targets_calls] == [REPO_SELECTOR]


def test_project_scoped_read_reaches_the_port_with_the_project(targets: RecordingTargetService) -> None:
    result = CliRunner().invoke(app, ["--project", PETS.name or "", "improvement-jobs", "list"])

    assert result.exit_code == 0
    assert [(call.repo, call.project) for call in targets.read_targets_calls] == [(None, "Pets")]


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
    assert getattr(params["--repo"], "help", None)


@pytest.mark.parametrize("command", [["status"], ["audit", "start"], ["recon", "start"]])
def test_single_repository_commands_keep_repo_positional(command: list[str]) -> None:
    result = CliRunner().invoke(app, [*command, "--help"])

    assert result.exit_code == 0
    help_text = _rendered(result.stdout)
    assert "[OPTIONS]" in help_text
    assert "--repo" not in help_text


def test_batch_write_scope_stays_mutually_exclusive(targets: RecordingTargetService) -> None:
    result = CliRunner().invoke(app, ["email", "set", "--manual", "on", "--repo", REPO_SELECTOR, "--all-repos"])

    assert result.exit_code == 1
    assert "--repo cannot be combined with --all-repos or --all-projects" in result.stderr
    assert targets.write_targets_calls == []
