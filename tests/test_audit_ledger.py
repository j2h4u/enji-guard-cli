from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from enji_guard_cli.audit.errors import AuditNotFoundError, AuditUpstreamError
from enji_guard_cli.audit.ledger import FileAuditLedger, new_entry
from enji_guard_cli.audit.ports import AuditRun, AuditRunStart, AuditTaskDetail


def _entry(**kwargs: object):
    return new_entry(
        AuditRunStart(
            cast(str, kwargs["repo_id"]),
            cast(str, kwargs["project_id"]),
            cast(str, kwargs["audit_key"]),
            cast(str | None, kwargs["task_id"]),
            cast(str | None, kwargs["task_status"]),
            cast(str | None, kwargs["current_head_sha"]),
            cast(str | None, kwargs["audited_head_sha"]),
        ),
        observed_at=cast(datetime, kwargs["observed_at"]),
        started_at=cast(str | None, kwargs.get("started_at")),
        ttl_seconds=cast(int, kwargs.get("ttl_seconds", 21600)),
    )


def test_new_entry_maps_started_value_and_normalizes_ttl() -> None:
    observed = datetime(2026, 1, 1, 3, tzinfo=UTC)
    entry = new_entry(
        AuditRunStart("repo", "project", "audit.security", "task", "queued", "new", "old"),
        observed_at=observed,
        started_at="2026-01-01T03:00:01Z",
        ttl_seconds=60,
    )
    assert entry.repo_id == "repo"
    assert entry.project_id == "project"
    assert entry.audit_key == "audit.security"
    assert entry.audited_head_sha == "old"
    assert entry.observed_at == observed
    assert entry.expires_at == observed + timedelta(seconds=60)
    assert entry.started_at == "2026-01-01T03:00:01Z"


def test_ledger_reconciles_task_by_id_and_prunes_terminal(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    ledger.record_started(
        _entry(
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
        _entry(
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
        _entry(
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


def test_reconcile_refreshes_each_task_id_even_when_action_is_upstream(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    for task_id in ("task-1", "task-2"):
        ledger.record_started(
            _entry(
                repo_id="repo",
                project_id="project",
                audit_key="audit.security",
                task_id=task_id,
                task_status="queued",
                current_head_sha=None,
                audited_head_sha=None,
                observed_at=now,
            )
        )
    looked_up: list[str] = []

    def lookup(task_id: str) -> AuditTaskDetail:
        looked_up.append(task_id)
        return AuditTaskDetail(task_id, "running", started_at="2026-01-01T00:01:00+00:00")

    runs = ledger.reconcile(
        "repo",
        (AuditRun("task-1", "audit.security", "running", None, "2026-01-01T00:01:00+00:00", None),),
        lookup,
        now=now,
    )

    assert looked_up == ["task-1", "task-2"]
    assert {run.task_id for run in runs} == {"task-1", "task-2"}


def test_reconcile_terminal_detail_suppresses_only_same_task_id(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    ledger.record_started(
        _entry(
            repo_id="repo",
            project_id="project",
            audit_key="audit.security",
            task_id="task-done",
            task_status="running",
            current_head_sha=None,
            audited_head_sha=None,
            observed_at=now,
        )
    )
    upstream = (
        AuditRun("task-done", "audit.security", "running", None, None, None),
        AuditRun("task-new", "audit.security", "running", None, None, None),
    )

    runs = ledger.reconcile(
        "repo",
        upstream,
        lambda task_id: AuditTaskDetail(task_id, "completed", completed_at="2026-01-01T00:02:00+00:00"),
        now=now,
    )

    assert [run.task_id for run in runs] == ["task-new"]
    assert ledger.active_for("repo") == ()


def test_reconcile_keeps_transient_lookup_guard_then_expires_it(tmp_path: Path) -> None:
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json", lookup_grace_seconds=60)
    ledger.record_started(
        _entry(
            repo_id="repo",
            project_id="project",
            audit_key="audit.security",
            task_id="task-1",
            task_status="running",
            current_head_sha=None,
            audited_head_sha=None,
            observed_at=observed,
        )
    )
    upstream = (AuditRun("task-1", "audit.security", "running", None, None, None),)

    def missing(_: str) -> AuditTaskDetail:
        raise AuditNotFoundError("not visible yet")

    within_grace = ledger.reconcile("repo", upstream, missing, now=observed + timedelta(seconds=30))
    after_grace = ledger.reconcile("repo", upstream, missing, now=observed + timedelta(seconds=61))

    assert [run.task_id for run in within_grace] == ["task-1"]
    assert [run.task_id for run in after_grace] == ["task-1"]
    assert ledger.active_for("repo") == ()


def test_reconcile_transient_lookup_failure_keeps_guard_until_ttl(tmp_path: Path) -> None:
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json", ttl_seconds=120, lookup_grace_seconds=1)
    ledger.record_started(
        _entry(
            repo_id="repo",
            project_id="project",
            audit_key="audit.security",
            task_id="task-1",
            task_status="running",
            current_head_sha=None,
            audited_head_sha=None,
            observed_at=observed,
            ttl_seconds=120,
        )
    )

    def unavailable(_: str) -> AuditTaskDetail:
        raise AuditUpstreamError("service unavailable")

    runs = ledger.reconcile("repo", (), unavailable, now=observed + timedelta(seconds=90))
    assert [run.task_id for run in runs] == ["task-1"]
    assert ledger.active_for("repo", now=observed + timedelta(seconds=90))

    assert ledger.reconcile("repo", (), unavailable, now=observed + timedelta(seconds=121)) == ()
    assert ledger.active_for("repo", now=observed + timedelta(seconds=121)) == ()


def test_idless_guard_survives_terminal_history(tmp_path: Path) -> None:
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    ledger.record_started(
        _entry(
            repo_id="repo",
            project_id="project",
            audit_key="audit.security",
            task_id=None,
            task_status="running",
            current_head_sha=None,
            audited_head_sha=None,
            observed_at=observed,
        )
    )

    def unexpected(_: str) -> AuditTaskDetail:
        raise AssertionError("id-less entries must not be looked up")

    runs = ledger.reconcile(
        "repo",
        (AuditRun("old", "audit.security", "completed", None, None, "done"),),
        unexpected,
        now=observed,
    )

    assert {run.task_id for run in runs} == {None, "old"}
    assert ledger.active_for("repo", now=observed)


def test_reconcile_expired_entry_does_not_lookup(tmp_path: Path) -> None:
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    ledger.record_started(
        _entry(
            repo_id="repo",
            project_id="project",
            audit_key="audit.security",
            task_id="expired",
            task_status="running",
            current_head_sha=None,
            audited_head_sha=None,
            observed_at=observed,
            ttl_seconds=10,
        )
    )
    looked_up: list[str] = []

    def lookup(task_id: str) -> AuditTaskDetail:
        looked_up.append(task_id)
        return AuditTaskDetail(task_id, "running")

    assert ledger.reconcile("repo", (), lookup, now=observed + timedelta(seconds=11)) == ()
    assert looked_up == []


def test_reconcile_looks_up_duplicate_task_id_once(tmp_path: Path) -> None:
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    first = _entry(
        repo_id="repo",
        project_id="project",
        audit_key="audit.security",
        task_id="same-task",
        task_status="queued",
        current_head_sha=None,
        audited_head_sha=None,
        observed_at=observed,
    )
    second = _entry(
        repo_id="repo",
        project_id="project",
        audit_key="audit.security",
        task_id="same-task",
        task_status="running",
        current_head_sha=None,
        audited_head_sha=None,
        observed_at=observed + timedelta(seconds=1),
    )
    ledger._write((first, second))
    calls: list[str] = []

    def lookup(task_id: str) -> AuditTaskDetail:
        calls.append(task_id)
        return AuditTaskDetail(task_id, "running", started_at="2026-01-01T00:01:00+00:00")

    runs = ledger.reconcile("repo", (), lookup, now=observed + timedelta(seconds=2))
    assert calls == ["same-task"]
    assert len(runs) == 1
    assert runs[0].task_id == "same-task"


@pytest.mark.parametrize("failure", [AuditNotFoundError("not found"), AuditUpstreamError("unavailable")])
def test_reconcile_caches_duplicate_task_id_failures(tmp_path: Path, failure: Exception) -> None:
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json", lookup_grace_seconds=60)
    entries = tuple(
        _entry(
            repo_id="repo",
            project_id="project",
            audit_key=action,
            task_id="same-task",
            task_status="running",
            current_head_sha=None,
            audited_head_sha=None,
            observed_at=observed,
        )
        for action in ("audit.security", "audit.tests")
    )
    ledger._write(entries)
    calls: list[str] = []

    def lookup(task_id: str) -> AuditTaskDetail:
        calls.append(task_id)
        raise failure

    runs = ledger.reconcile("repo", (), lookup, now=observed + timedelta(seconds=30))
    assert calls == ["same-task"]
    assert [run.task_id for run in runs] == ["same-task"]


def test_reconcile_same_task_dedupe_includes_output_fields(tmp_path: Path) -> None:
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    first = AuditRun(
        "same-task",
        "audit.security",
        "running",
        "2026-01-01T00:01:00+00:00",
        None,
        None,
        projection_source="active_runs",
        current_head_sha="head-a",
    )
    second = AuditRun(
        "same-task",
        "audit.tests",
        "running",
        "2026-01-01T00:01:00+00:00",
        None,
        None,
        projection_source="task_by_id",
        current_head_sha="head-b",
    )

    forward = ledger.reconcile(
        "repo", (first, second), lambda task_id: AuditTaskDetail(task_id, "running"), now=observed
    )
    reverse = ledger.reconcile(
        "repo", (second, first), lambda task_id: AuditTaskDetail(task_id, "running"), now=observed
    )
    assert forward == reverse


@pytest.mark.parametrize(
    "upstream",
    [
        (
            AuditRun(
                "same-task",
                "audit.security",
                "completed",
                "2026-01-01T00:00:00+00:00",
                None,
                "2026-01-01T00:02:00+00:00",
            ),
            AuditRun(
                "same-task", "audit.security", "running", "2026-01-01T00:01:00+00:00", "2026-01-01T00:01:00+00:00", None
            ),
        ),
        (
            AuditRun(
                "same-task", "audit.security", "running", "2026-01-01T00:01:00+00:00", "2026-01-01T00:01:00+00:00", None
            ),
            AuditRun(
                "same-task",
                "audit.security",
                "completed",
                "2026-01-01T00:00:00+00:00",
                None,
                "2026-01-01T00:02:00+00:00",
            ),
        ),
    ],
)
def test_reconcile_same_task_id_reduction_is_independent_of_input_order(
    tmp_path: Path, upstream: tuple[AuditRun, AuditRun]
) -> None:
    ledger = FileAuditLedger(tmp_path / "ledger.json")

    runs = ledger.reconcile("repo", upstream, lambda task_id: AuditTaskDetail(task_id, "running"))

    assert runs == (upstream[0] if upstream[0].status == "running" else upstream[1],)
