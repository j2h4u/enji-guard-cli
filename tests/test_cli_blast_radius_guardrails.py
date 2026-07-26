"""Irreversible writes must be confirmed: --all-projects, project delete, repo remove.

Refusal is asserted as "the port was never reached": no scope expansion for a
batch write, no delete call on the Portfolio gateway.  Acceptance is asserted
as the exact identifiers the gateway received, so a facade that deletes the
wrong project or detaches the wrong repository fails here.
"""

import importlib

import pytest
import typer
from typer.testing import CliRunner

from application_builder import (
    BIRDS,
    PETS,
    RecordingAuditGateway,
    RecordingPortfolioGateway,
    RecordingTargetService,
    recording_application,
    repository,
)
from enji_guard_cli.audit.ports import AuditAutofixJob
from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.portfolio.models import ProjectDetail

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

BATCH_WRITES = [
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

BIRDS_REPOSITORY = repository("owner/name", repo_id="r2", project=BIRDS)


class Ports:
    """The two ports a blast-radius guardrail can be observed through."""

    def __init__(self) -> None:
        self.targets = RecordingTargetService((BIRDS_REPOSITORY,), (PETS, BIRDS))
        self.portfolio = RecordingPortfolioGateway((ProjectDetail(PETS, ()), ProjectDetail(BIRDS, (BIRDS_REPOSITORY,))))


@pytest.fixture
def ports(monkeypatch: pytest.MonkeyPatch) -> Ports:
    installed = Ports()
    application = recording_application(
        audit=RecordingAuditGateway(autofix_jobs={"r2": EXISTING_JOBS}),
        portfolio=installed.portfolio,
        targets=installed.targets,
    )
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: application)
    return installed


@pytest.mark.parametrize("command", BATCH_WRITES)
def test_all_projects_without_confirmation_is_refused(ports: Ports, command: list[str]) -> None:
    result = CliRunner().invoke(app, [*command, "--all-projects"])

    assert result.exit_code == 1
    assert "CONFIRMATION_REQUIRED" in result.stderr
    assert "--yes" in result.stderr
    assert ports.targets.write_targets_calls == []


@pytest.mark.parametrize("command", BATCH_WRITES)
def test_all_projects_with_yes_proceeds(ports: Ports, command: list[str]) -> None:
    result = CliRunner().invoke(app, [*command, "--all-projects", "--yes"])

    assert result.exit_code == 0
    call = ports.targets.write_targets_calls[-1]
    assert (call.all_repos, call.all_projects) == (False, True)
    assert (call.repo, call.project) == (None, None)


def test_json_mode_never_prompts_and_reports_a_json_envelope(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["--json", "email", "set", "--manual", "on", "--all-projects"])

    assert result.exit_code == 1
    assert result.stderr.lstrip().startswith("{")
    assert "CONFIRMATION_REQUIRED" in result.stderr
    assert ports.targets.write_targets_calls == []


def test_bounded_scopes_stay_unconfirmed(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["--project", "Birds", "email", "set", "--manual", "on", "--all-repos"])

    assert result.exit_code == 0
    call = ports.targets.write_targets_calls[-1]
    assert (call.all_repos, call.all_projects, call.project) == (True, False, "Birds")


def test_interactive_operator_is_prompted_and_can_decline(ports: Ports, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "_is_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda _message: False)

    result = CliRunner().invoke(app, ["email", "set", "--manual", "on", "--all-projects"])

    assert result.exit_code == 1
    assert "ABORTED" in result.stderr
    assert ports.targets.write_targets_calls == []


def test_interactive_operator_can_accept(ports: Ports, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "_is_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda _message: True)

    result = CliRunner().invoke(app, ["email", "set", "--manual", "on", "--all-projects"])

    assert result.exit_code == 0
    call = ports.targets.write_targets_calls[-1]
    assert (call.all_repos, call.all_projects) == (False, True)


def _deletions(ports: Ports) -> list[tuple[str, ...]]:
    """Every irreversible removal the Portfolio gateway actually performed."""
    return [("delete_project", project) for project in ports.portfolio.deleted_projects] + [
        ("remove_repository", project_id, repo_id) for project_id, repo_id in ports.portfolio.removed_repositories
    ]


DELETIONS = [
    (["project", "delete", "Pets"], ("delete_project", "p1")),
    (["repo", "remove", "github@github.com:owner/name"], ("remove_repository", "p2", "r2")),
]


@pytest.mark.parametrize(("command", "expected"), DELETIONS)
def test_deletion_without_confirmation_is_refused(ports: Ports, command: list[str], expected: tuple[str, ...]) -> None:
    del expected
    result = CliRunner().invoke(app, command)

    assert result.exit_code == 1
    assert "CONFIRMATION_REQUIRED" in result.stderr
    assert "--yes" in result.stderr
    assert _deletions(ports) == []


@pytest.mark.parametrize(("command", "expected"), DELETIONS)
def test_deletion_with_yes_removes_exactly_the_named_target(
    ports: Ports, command: list[str], expected: tuple[str, ...]
) -> None:
    result = CliRunner().invoke(app, [*command, "--yes"])

    assert result.exit_code == 0
    assert _deletions(ports) == [expected]


@pytest.mark.parametrize(("command", "expected"), DELETIONS)
def test_deletion_in_json_mode_never_prompts(ports: Ports, command: list[str], expected: tuple[str, ...]) -> None:
    del expected
    result = CliRunner().invoke(app, ["--json", *command])

    assert result.exit_code == 1
    assert result.stderr.lstrip().startswith("{")
    assert "CONFIRMATION_REQUIRED" in result.stderr
    assert _deletions(ports) == []


@pytest.mark.parametrize(("command", "expected"), DELETIONS)
def test_interactive_operator_can_decline_a_deletion(
    ports: Ports, monkeypatch: pytest.MonkeyPatch, command: list[str], expected: tuple[str, ...]
) -> None:
    del expected
    monkeypatch.setattr(cli_module, "_is_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda _message: False)

    result = CliRunner().invoke(app, command)

    assert result.exit_code == 1
    assert "ABORTED" in result.stderr
    assert _deletions(ports) == []


@pytest.mark.parametrize(("command", "expected"), DELETIONS)
def test_interactive_operator_can_accept_a_deletion(
    ports: Ports, monkeypatch: pytest.MonkeyPatch, command: list[str], expected: tuple[str, ...]
) -> None:
    monkeypatch.setattr(cli_module, "_is_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda _message: True)

    result = CliRunner().invoke(app, command)

    assert result.exit_code == 0
    assert _deletions(ports) == [expected]
