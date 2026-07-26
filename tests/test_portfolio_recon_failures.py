"""Recon must report the start that actually happened, not a hardcoded success.

``start_recon`` used to stamp ``state="started"`` on whatever the audit start
port returned, so an upstream refusal reached the operator as a success with an
empty ``task_id`` -- on both ``recon start`` and the ``repo add`` continuation.
These tests pin the outcome and its reason end to end through the real CLI, and
pin the states that were already correct so restoring the lie cannot pass.
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
    RecordingTargetService,
    recording_application,
    repository,
)
from enji_guard_cli.audit.errors import AuditUpstreamError
from enji_guard_cli.audit.ports import AuditRun
from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.portfolio.models import ProjectDetail

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

REPO = "github@github.com:acme/cat"
NEW_REPO = "github@github.com:acme/dog"
REFUSAL = "recon start refused for r1: repository is not reachable"


def _install(monkeypatch: pytest.MonkeyPatch, **ports: object) -> None:
    monkeypatch.setattr(
        cli_module,
        "_application",
        lambda auth_file=None: recording_application(**ports),  # pyright: ignore[reportArgumentType]
    )


def _payload(stdout: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(stdout))


def test_recon_start_reports_the_upstream_refusal_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, audit=RecordingAuditGateway(start_failure=AuditUpstreamError(REFUSAL)))

    result = CliRunner().invoke(app, ["recon", "start", REPO, "--json"])

    assert result.exit_code == 0
    payload = _payload(result.stdout)
    assert payload["state"] == "failed"
    assert payload["reason"] == REFUSAL
    assert "task_id" not in payload


def test_recon_start_reports_the_upstream_refusal_without_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, audit=RecordingAuditGateway(start_failure=AuditUpstreamError(REFUSAL)))

    result = CliRunner().invoke(app, ["recon", "start", REPO])

    assert result.exit_code == 0
    assert "state: failed" in result.stdout
    assert REFUSAL in result.stdout


def test_repo_add_still_reports_the_repository_while_showing_the_recon_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The membership write succeeded; only its recon continuation did not."""
    portfolio = RecordingPortfolioGateway((ProjectDetail(PETS, ()),), added_recon_done=None)
    _install(
        monkeypatch,
        audit=RecordingAuditGateway(start_failure=AuditUpstreamError(REFUSAL)),
        portfolio=portfolio,
        targets=RecordingTargetService(()),
    )

    result = CliRunner().invoke(app, ["repo", "add", NEW_REPO, "--json"])

    assert result.exit_code == 0
    payload = _payload(result.stdout)
    assert payload["state"] == "added"
    assert cast(dict[str, object], payload["repository"])["repo_id"] == "added-1"
    recon = cast(dict[str, object], payload["recon"])
    assert recon["state"] == "failed"
    assert recon["reason"] == REFUSAL


def test_repo_add_recon_failure_is_readable_without_json(monkeypatch: pytest.MonkeyPatch) -> None:
    portfolio = RecordingPortfolioGateway((ProjectDetail(PETS, ()),), added_recon_done=None)
    _install(
        monkeypatch,
        audit=RecordingAuditGateway(start_failure=AuditUpstreamError(REFUSAL)),
        portfolio=portfolio,
        targets=RecordingTargetService(()),
    )

    result = CliRunner().invoke(app, ["repo", "add", NEW_REPO])

    assert result.exit_code == 0
    assert "state: added" in result.stdout
    assert '"state": "failed"' in result.stdout
    assert REFUSAL in result.stdout


def test_a_successful_recon_start_still_reports_started_with_its_task(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, audit=RecordingAuditGateway())

    result = CliRunner().invoke(app, ["recon", "start", REPO, "--json"])

    assert result.exit_code == 0
    payload = _payload(result.stdout)
    assert payload["state"] == "started"
    assert payload["task_id"] == "task-1"
    assert payload["task_status"] == "queued"
    assert "reason" not in payload


def test_recon_start_reports_already_running_from_the_live_run(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = RecordingAuditGateway(
        active_runs={"r1": (AuditRun("task-live", "audit.recon", "running", "t0", "t0", None),)}
    )
    _install(monkeypatch, audit=gateway)

    result = CliRunner().invoke(app, ["recon", "start", REPO, "--json"])

    assert result.exit_code == 0
    payload = _payload(result.stdout)
    assert payload["state"] == "already_running"
    assert payload["task_id"] == "task-live"
    assert gateway.started == []


def test_recon_start_reports_unchanged_when_baseline_is_already_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = RecordingAuditGateway()
    _install(monkeypatch, audit=gateway, targets=RecordingTargetService((repository(recon_done=True),)))

    result = CliRunner().invoke(app, ["recon", "start", REPO, "--json"])

    assert result.exit_code == 0
    payload = _payload(result.stdout)
    assert payload["state"] == "unchanged"
    assert gateway.started == []
