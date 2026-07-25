"""Audit selectors and --all must never be silently reconciled behind the operator.

A selector is only honored if it decides which audit runs upstream, so the
assertions read the audit gateway: which run requests were issued, and which
report histories were fetched.
"""

import importlib

import pytest
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from application_builder import RecordingAuditGateway, recording_application
from enji_guard_cli.audit.ports import AuditArtifact, AuditReportRef
from enji_guard_cli.delivery.cli.app import app

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

REPO = "github@github.com:acme/cat"


@pytest.fixture
def audit_gateway(monkeypatch: pytest.MonkeyPatch) -> RecordingAuditGateway:
    gateway = RecordingAuditGateway(
        reports={("r1", "tests"): (AuditReportRef("task-tests", "2026-07-20T00:00:00Z", None, True),)},
        artifacts={("r1", "audit.tests"): AuditArtifact("audit.tests", "# Tests", 73, "2026-07-20T00:00:00Z")},
    )
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: recording_application(audit=gateway))
    return gateway


@pytest.mark.parametrize("command", ["start", "read"])
def test_all_with_explicit_selectors_is_rejected_before_any_call(
    audit_gateway: RecordingAuditGateway, command: str
) -> None:
    result = CliRunner().invoke(app, ["audit", command, REPO, "security", "--all"])

    assert result.exit_code == 1
    assert "pass audit selectors or --all, not both" in result.stderr
    assert audit_gateway.catalog_calls == 0
    assert audit_gateway.started == []
    assert audit_gateway.listed_reports == []


def test_audit_start_with_all_starts_every_published_audit(audit_gateway: RecordingAuditGateway) -> None:
    result = CliRunner().invoke(app, ["audit", "start", REPO, "--all"])

    assert result.exit_code == 0
    assert [request.action_key for request in audit_gateway.started] == ["audit.security", "audit.tests"]
    assert {request.repo_id for request in audit_gateway.started} == {"r1"}


def test_audit_start_with_one_selector_starts_only_that_audit(audit_gateway: RecordingAuditGateway) -> None:
    result = CliRunner().invoke(app, ["audit", "start", REPO, "audit.security"])

    assert result.exit_code == 0
    assert [request.action_key for request in audit_gateway.started] == ["audit.security"]


def test_audit_summary_has_no_all_flag() -> None:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    audit = root.commands["audit"]
    assert isinstance(audit, TyperGroup)
    summary = audit.commands["summary"]
    assert "--all" not in {option for param in summary.params for option in param.opts}

    result = CliRunner().invoke(app, ["audit", "summary", REPO, "--all"])

    assert result.exit_code == 2


def test_audit_summary_without_selectors_summarizes_everything(audit_gateway: RecordingAuditGateway) -> None:
    result = CliRunner().invoke(app, ["audit", "summary", REPO])

    assert result.exit_code == 0
    assert audit_gateway.listed_reports == [("r1", "vulns"), ("r1", "tests")]


def test_audit_summary_with_one_selector_reads_only_that_metric_group(
    audit_gateway: RecordingAuditGateway,
) -> None:
    result = CliRunner().invoke(app, ["audit", "summary", REPO, "tests"])

    assert result.exit_code == 0
    assert audit_gateway.listed_reports == [("r1", "tests")]
