"""Project and repository membership commands, asserted at the Portfolio gateway.

Every command here hands the gateway two or more identifiers of the same type
-- a project id and a repository id, or a source and a target project.  The CLI
output cannot tell a correct call from a transposed one, so each test pins the
argument positions the gateway actually received.
"""

import importlib

import pytest
from typer.testing import CliRunner

from application_builder import (
    BIRDS,
    PETS,
    AddedRepository,
    MovedRepository,
    RecordingAuditGateway,
    RecordingPortfolioGateway,
    RecordingTargetService,
    recording_application,
    repository,
)
from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.portfolio.models import AccountPreferences, ProjectDetail, RepositoryIdentity, RepositoryProvider

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

CAT = repository("acme/cat")
CAT_SELECTOR = "github@github.com:acme/cat"


class Ports:
    def __init__(self) -> None:
        self.portfolio = RecordingPortfolioGateway((ProjectDetail(PETS, (CAT,)), ProjectDetail(BIRDS, ())))
        self.targets = RecordingTargetService((CAT,), (PETS, BIRDS))
        self.audit = RecordingAuditGateway()


@pytest.fixture
def ports(monkeypatch: pytest.MonkeyPatch) -> Ports:
    installed = Ports()
    application = recording_application(audit=installed.audit, portfolio=installed.portfolio, targets=installed.targets)
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: application)
    return installed


def test_project_create_sends_the_validated_name(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["project", "create", "Cats"])

    assert result.exit_code == 0
    assert ports.portfolio.created_projects == ["Cats"]


def test_project_create_is_repeat_safe_for_an_existing_name(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["project", "create", "pets"])

    assert result.exit_code == 0
    assert "already_present" in result.stdout
    assert ports.portfolio.created_projects == []


def test_project_rename_sends_the_resolved_id_and_the_new_name(ports: Ports) -> None:
    """The gateway takes an id and a name; both are plain strings."""
    result = CliRunner().invoke(app, ["project", "rename", "Pets", "Dogs"])

    assert result.exit_code == 0
    assert ports.portfolio.renamed_projects == [("p1", "Dogs")]


def test_project_list_is_served_by_the_gateway(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["project", "list"])

    assert result.exit_code == 0
    assert "Pets" in result.stdout
    assert "Birds" in result.stdout


def test_repo_add_places_the_repository_in_the_selected_project(ports: Ports) -> None:
    result = CliRunner().invoke(
        app,
        [
            "repo",
            "add",
            "github@github.com:acme/new",
            "--project",
            "Birds",
            "--repo-access-credential-id",
            "cred-1",
        ],
    )

    assert result.exit_code == 0
    expected = AddedRepository("p2", RepositoryIdentity(RepositoryProvider.GITHUB, "acme/new", "github.com"), "cred-1")
    assert ports.portfolio.added_repositories == [expected]


def test_repo_add_connects_an_existing_disconnected_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    disconnected = repository("acme/cat", connected=False)
    portfolio = RecordingPortfolioGateway((ProjectDetail(PETS, (disconnected,)),))
    monkeypatch.setattr(
        cli_module,
        "_application",
        lambda auth_file=None: recording_application(
            portfolio=portfolio, targets=RecordingTargetService((disconnected,))
        ),
    )

    result = CliRunner().invoke(app, ["repo", "add", CAT_SELECTOR, "--project", "Pets"])

    assert result.exit_code == 0
    assert portfolio.connected_repositories == [("p1", "r1")]
    assert portfolio.added_repositories == []


def test_repo_move_keeps_source_and_target_projects_apart(ports: Ports) -> None:
    """Source and target are both project ids; transposing them is silent."""
    result = CliRunner().invoke(app, ["repo", "move", CAT_SELECTOR, "--to-project", "Birds", "--project", "Pets"])

    assert result.exit_code == 0
    assert ports.portfolio.preflights == [MovedRepository("p1", "r1", "p2")]
    assert ports.portfolio.moved_repositories == [MovedRepository("p1", "r1", "p2")]


def test_repo_move_into_the_current_project_writes_nothing(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["repo", "move", CAT_SELECTOR, "--to-project", "Pets"])

    assert result.exit_code == 0
    assert "already_in_target" in result.stdout
    assert ports.portfolio.moved_repositories == []


def test_repo_resolve_asks_the_target_service_for_the_named_selector(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["repo", "resolve", CAT_SELECTOR, "--project", "Pets", "--json"])

    assert result.exit_code == 0
    assert [(call.selector, call.project) for call in ports.targets.resolve_repository_calls] == [
        (CAT_SELECTOR, "Pets")
    ]


def test_recon_start_starts_the_recon_action_for_the_resolved_repository(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["recon", "start", CAT_SELECTOR, "--project", "Pets"])

    assert result.exit_code == 0
    assert [(item.repo_id, item.project_id, item.action_key) for item in ports.audit.started] == [
        ("r1", "p1", "audit.recon")
    ]


def test_recon_start_is_skipped_when_baseline_discovery_is_done(monkeypatch: pytest.MonkeyPatch) -> None:
    done = repository("acme/cat", recon_done=True)
    audit = RecordingAuditGateway()
    monkeypatch.setattr(
        cli_module,
        "_application",
        lambda auth_file=None: recording_application(
            audit=audit,
            portfolio=RecordingPortfolioGateway((ProjectDetail(PETS, (done,)),)),
            targets=RecordingTargetService((done,)),
        ),
    )

    result = CliRunner().invoke(app, ["recon", "start", CAT_SELECTOR])

    assert result.exit_code == 0
    assert "unchanged" in result.stdout
    assert audit.started == []


def test_language_show_reads_account_preferences(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["language", "show", "--json"])

    assert result.exit_code == 0
    assert '"language": "en"' in result.stdout


def test_language_set_writes_account_preferences(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["language", "set", "ru"])

    assert result.exit_code == 0
    assert ports.portfolio.written_preferences == [AccountPreferences("ru")]
