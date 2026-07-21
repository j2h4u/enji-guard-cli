from datetime import UTC, datetime
from pathlib import Path

from enji_guard_cli.audit.ledger import FileAuditLedger, new_entry
from enji_guard_cli.audit.ports import AuditTaskDetail


def test_ledger_reconciles_task_by_id_and_prunes_terminal(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    ledger.record_started(
        new_entry(
            repo_id="repo",
            project_id="project",
            audit_key="audit.security",
            task_id="task",
            task_status="queued",
            current_head_sha="new",
            audited_head_sha="old",
            observed_at=now,
        )
    )
    runs = ledger.reconcile("repo", (), lambda task: AuditTaskDetail(task, "running", started_at="now"), now=now)
    assert runs[0].projection_status_source == "task_by_id"
    ledger.record_started(
        new_entry(
            repo_id="repo",
            project_id="project",
            audit_key="audit.tests",
            task_id="done",
            task_status="completed",
            current_head_sha="new",
            audited_head_sha="new",
            observed_at=now,
        )
    )
    assert ledger.prune(now=now) == 1


def test_ledger_prunes_fresh_started_entry(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    ledger.record_started(
        new_entry(
            repo_id="r",
            project_id="p",
            audit_key="audit.x",
            task_id=None,
            task_status="running",
            current_head_sha="sha",
            audited_head_sha=None,
            observed_at=now,
        )
    )
    assert ledger.prune(now=now, current_head_sha="sha", audited_head_shas={"audit.x": "sha"}) == 1
