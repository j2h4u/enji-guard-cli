"""Contract tests for the shared sequential batch-mutation application result."""

import importlib

from typer.testing import CliRunner

from application_builder import CATALOG, RecordingAuditGateway, RecordingTargetService, recording_application
from enji_guard_cli.application import EmailPreferencesWriteRequest
from enji_guard_cli.application.mutations import (
    MutationDecision,
    MutationOperationalError,
    MutationReason,
    MutationTargetView,
    execute_batch,
)
from enji_guard_cli.application.portfolio_views import repository_ref_view
from enji_guard_cli.audit.ports import AuditEmailPreference, AuditSchedule
from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.errors import EnjiApiError

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")


def _target() -> MutationTargetView:
    target = RecordingTargetService().repositories[0]
    return MutationTargetView(repository_ref_view(target), "security")


def test_executor_is_empty_completed_and_fail_fast_with_exact_counts() -> None:
    first = MutationDecision(_target(), "changed", MutationReason.APPLIED, lambda: None)
    failed = MutationDecision(
        _target(),
        "changed",
        MutationReason.APPLIED,
        lambda: _fail("UPSTREAM", "write stopped after dispatch"),
    )
    never_called = MutationDecision(
        _target(), "changed", MutationReason.APPLIED, lambda: (_ for _ in ()).throw(AssertionError("must not run"))
    )

    empty = execute_batch(())
    result = execute_batch((first, failed, never_called))

    assert (empty.status, empty.total, empty.completed, empty.remaining, empty.results) == ("completed", 0, 0, 0, ())
    assert (result.status, result.total, result.completed, result.remaining) == ("partial", 3, 1, 2)
    assert (result.changed, result.unchanged, result.failed, len(result.results)) == (1, 0, 1, 2)
    assert result.results[-1].reason is MutationReason.OUTCOME_UNKNOWN


def test_email_noop_reads_each_item_without_put_and_preserves_unspecified_field() -> None:
    audit = RecordingAuditGateway(
        catalog=CATALOG,
        email_preferences={
            ("r1", "audit.security"): AuditEmailPreference("audit.security", True, False),
            ("r1", "audit.tests"): AuditEmailPreference("audit.tests", True, True),
        },
    )
    application = recording_application(audit=audit)

    result = application.subscriptions.set_email_preferences(
        EmailPreferencesWriteRequest("github@github.com:acme/cat", None, manual=True)
    )

    assert (result.status, result.changed, result.unchanged, result.failed) == ("completed", 0, 2, 0)
    assert [item.reason for item in result.results] == [MutationReason.ALREADY_EFFECTIVE] * 2
    assert audit.email_writes == []


def test_partial_batch_is_emitted_then_uses_auth_exit_code(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class FailingGateway(RecordingAuditGateway):
        def set_schedule(self, repo_id: str, audit_key: str, schedule: AuditSchedule) -> AuditSchedule:
            if audit_key == "audit.tests":
                raise EnjiApiError("AUTH_REQUIRED", "expired credential")
            return super().set_schedule(repo_id, audit_key, schedule)

    application = recording_application(audit=FailingGateway(catalog=CATALOG))
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: application)

    result = CliRunner().invoke(
        app, ["--json", "schedule", "set", "--repo", "github@github.com:acme/cat", "--enabled", "on"]
    )

    assert result.exit_code == 3
    assert '"status": "partial"' in result.stdout
    assert '"remaining": 1' in result.stdout
    assert '"reason": "OUTCOME_UNKNOWN"' in result.stdout


def _fail(code: str, message: str) -> None:
    raise MutationOperationalError(code, message, outcome_unknown=True)
