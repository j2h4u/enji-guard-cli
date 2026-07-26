"""A failed audit start must say why, and only genuine refusals may be ``failed``.

``audit start`` bills for every run it issues, so ``state=failed`` without a
reason is unactionable.  These tests fix three things at once: a refusal
carries a reason all the way to ``--json``; a programming error in the start
path stays a real failure instead of becoming a per-audit outcome; and the
two contract failures Audit can detect locally are separated by blast radius --
a repository that cannot carry any run aborts the batch, while one catalog
action missing its metadata fails only itself.
"""

import importlib
import json
from typing import cast

import pytest
from typer.testing import CliRunner

from application_builder import (
    PETS,
    RecordingAuditGateway,
    RecordingPortfolioGateway,
    recording_application,
    repository,
)
from enji_guard_cli.audit.errors import AuditUpstreamError
from enji_guard_cli.audit.ports import AuditCatalogAction, AuditCatalogResult
from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.portfolio.models import ProjectDetail

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

REPO = "github@github.com:acme/cat"

RECON = AuditCatalogAction("audit.recon", "Recon", "workflow", "draft", None, "recon", "runbook-recon", "recon", "1")
SECURITY = AuditCatalogAction(
    "audit.security", "Security", "audit", "published", "vulns", "audit", "runbook-security", "security", "1"
)
TESTS_WITHOUT_SCHEMA = AuditCatalogAction(
    "audit.tests", "Tests", "audit", "published", "tests", "audit", "runbook-tests", None, "1"
)


def _install(monkeypatch: pytest.MonkeyPatch, **ports: object) -> None:
    monkeypatch.setattr(
        cli_module,
        "_application",
        lambda auth_file=None: recording_application(**ports),  # pyright: ignore[reportArgumentType]
    )


def _results(stdout: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], cast(dict[str, object], json.loads(stdout))["results"])


def test_upstream_refusal_reports_failed_with_the_upstream_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = RecordingAuditGateway(start_failure=AuditUpstreamError("start refused for audit.security: quota spent"))
    _install(monkeypatch, audit=gateway)

    result = CliRunner().invoke(app, ["audit", "start", REPO, "security", "--json"])

    assert result.exit_code == 0
    item = _results(result.stdout)[0]
    assert item["state"] == "failed"
    assert item["reason"] == "start refused for audit.security: quota spent"


def test_upstream_refusal_reason_is_readable_without_json(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = RecordingAuditGateway(start_failure=AuditUpstreamError("start refused for audit.security: quota spent"))
    _install(monkeypatch, audit=gateway)

    result = CliRunner().invoke(app, ["audit", "start", REPO, "security"])

    assert result.exit_code == 0
    assert "state=failed" in result.stdout
    assert "quota spent" in result.stdout


def test_programming_error_in_the_start_path_is_not_reported_as_a_failed_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ``AttributeError`` from a collaborator is a defect, not a refusal."""
    gateway = RecordingAuditGateway(start_failure=AttributeError("'NoneType' object has no attribute 'task_body'"))
    _install(monkeypatch, audit=gateway)

    result = CliRunner().invoke(app, ["audit", "start", REPO, "security", "--json"])

    assert result.exit_code != 0
    assert isinstance(result.exception, AttributeError)


def test_disconnected_repository_aborts_the_whole_batch_with_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every selected audit would fail for the same reason, so none is attempted."""
    gateway = RecordingAuditGateway()
    portfolio = RecordingPortfolioGateway((ProjectDetail(PETS, (repository(connected=False),)),))
    _install(monkeypatch, audit=gateway, portfolio=portfolio)

    result = CliRunner().invoke(app, ["audit", "start", REPO, "--all"])

    assert result.exit_code == 1
    assert "repo is not connected" in result.stderr
    assert gateway.started == []


def test_one_action_without_an_artifact_schema_fails_alone_and_the_batch_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = RecordingAuditGateway(catalog=AuditCatalogResult(actions=(RECON, SECURITY, TESTS_WITHOUT_SCHEMA)))
    _install(monkeypatch, audit=gateway)

    result = CliRunner().invoke(app, ["audit", "start", REPO, "--all", "--json"])

    assert result.exit_code == 0
    items = {cast(str, item["action_key"]): item for item in _results(result.stdout)}
    assert items["audit.security"]["state"] == "started"
    assert items["audit.tests"]["state"] == "failed"
    assert items["audit.tests"]["reason"] == "audit task is missing artifact schema name"
    assert [request.action_key for request in gateway.started] == ["audit.security"]
