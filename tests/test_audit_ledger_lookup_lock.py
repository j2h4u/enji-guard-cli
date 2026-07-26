"""`reconcile` must not hold the ledger lock across upstream task lookups.

`task_lookup` is a network call.  Holding the cross-process `flock` for its
duration stalls every other thread and process that needs the ledger, including
the `record_started` guard that prevents a duplicate audit run.  These exercises
drive real subprocesses against one ledger file while a lookup is in flight; no
upstream service and no mocking is involved.
"""

import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from enji_guard_cli.audit.ledger import FileAuditLedger, _lock_path, new_entry
from enji_guard_cli.audit.ports import AuditLedgerEntry, AuditRun, AuditRunStart, AuditTaskDetail

ROOT = Path(__file__).parents[1]
OBSERVED = datetime(2026, 1, 1, tzinfo=UTC)
WORKER_TIMEOUT_SECONDS = 60.0

_LOCK_PROBE = textwrap.dedent(
    """
    import fcntl
    import sys
    from pathlib import Path

    lock_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])
    with lock_path.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result_path.write_text("blocked", encoding="utf-8")
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            result_path.write_text("acquired", encoding="utf-8")
    """
)

_RECORD_STARTED = textwrap.dedent(
    """
    import sys
    from datetime import UTC, datetime
    from pathlib import Path

    from enji_guard_cli.audit.ledger import FileAuditLedger, new_entry
    from enji_guard_cli.audit.ports import AuditRunStart

    FileAuditLedger(Path(sys.argv[1])).record_started(
        new_entry(
            AuditRunStart("repo", "project", sys.argv[2], sys.argv[3], "queued", None, None),
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    """
)

_PRUNE_MATCHING_HEAD = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    from enji_guard_cli.audit.ledger import FileAuditLedger

    removed = FileAuditLedger(Path(sys.argv[1])).prune(
        current_head_sha="head", audited_head_shas={sys.argv[2]: "head"}
    )
    assert removed == 1, f"expected one pruned entry, removed {removed}"
    """
)


def _entry(audit_key: str, task_id: str) -> AuditLedgerEntry:
    return new_entry(
        AuditRunStart("repo", "project", audit_key, task_id, "queued", None, None),
        observed_at=OBSERVED,
    )


def _run_worker(script: str, *args: str) -> None:
    """Run one short-lived helper interpreter to completion.

    The timeout is a hang guard, not a synchronization device: a helper that
    needs the ledger lock blocks forever if `reconcile` holds it across
    `task_lookup`, which is exactly the regression under test.  Correct code
    never comes near the budget, so a loaded CI box cannot flake it.
    """
    worker = subprocess.Popen(
        [sys.executable, "-c", script, *args],
        cwd=ROOT,
        env=os.environ | {"PYTHONPATH": str(ROOT / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = worker.communicate(timeout=WORKER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.communicate()
        raise AssertionError("ledger helper never finished; the ledger lock is held across task_lookup") from None
    assert worker.returncode == 0, f"ledger helper failed: {stdout}{stderr}"


def _probing_lookup(
    ledger_path: Path, results: list[str], *, during: Callable[[], None] = lambda: None
) -> Callable[[str], AuditTaskDetail]:
    """A `task_lookup` that reports whether the ledger lock was free while it ran."""

    def lookup(task_id: str) -> AuditTaskDetail:
        result_path = ledger_path.parent / f"probe-{task_id}"
        _run_worker(_LOCK_PROBE, str(_lock_path(ledger_path)), str(result_path))
        results.append(result_path.read_text(encoding="utf-8"))
        during()
        return AuditTaskDetail(task_id, "running")

    return lookup


def test_ledger_lock_is_free_while_a_task_lookup_runs(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger = FileAuditLedger(ledger_path)
    ledger.record_started(_entry("audit.security", "task-a"))
    ledger.record_started(_entry("audit.quality", "task-b"))

    results: list[str] = []
    runs = ledger.reconcile("repo", (), _probing_lookup(ledger_path, results), now=OBSERVED + timedelta(seconds=1))

    assert results == ["acquired", "acquired"], f"ledger lock was held across task_lookup: {results}"
    assert {run.task_id for run in runs} == {"task-a", "task-b"}


def test_entry_recorded_during_a_lookup_survives_reconcile(tmp_path: Path) -> None:
    """A concurrent `record_started` during network I/O must not be lost."""
    ledger_path = tmp_path / "ledger.json"
    ledger = FileAuditLedger(ledger_path)
    ledger.record_started(_entry("audit.security", "task-a"))

    def add_late_entry() -> None:
        if (ledger_path.parent / "late").exists():
            return
        (ledger_path.parent / "late").write_text("done", encoding="utf-8")
        _run_worker(_RECORD_STARTED, str(ledger_path), "audit.quality", "task-late")

    results: list[str] = []
    lookup = _probing_lookup(ledger_path, results, during=add_late_entry)
    runs = ledger.reconcile("repo", (), lookup, now=OBSERVED + timedelta(seconds=1))

    surviving = {entry.task_id for entry in ledger.active_for("repo", now=OBSERVED + timedelta(seconds=2))}
    assert surviving == {"task-a", "task-late"}, "the concurrently recorded guard was overwritten"
    # The next round notices the new task id and resolves it properly.
    assert {(run.task_id, run.projection_status_source) for run in runs} == {
        ("task-a", "task_by_id"),
        ("task-late", "task_by_id"),
    }


def test_entries_arriving_every_round_are_kept_under_the_ledger_projection(tmp_path: Path) -> None:
    """When lookups cannot converge, unresolved guards are retained, never dropped."""
    ledger_path = tmp_path / "ledger.json"
    ledger = FileAuditLedger(ledger_path)
    ledger.record_started(_entry("audit.security", "task-a"))
    arrivals = 0

    def add_another_entry() -> None:
        nonlocal arrivals
        arrivals += 1
        _run_worker(_RECORD_STARTED, str(ledger_path), f"audit.late.{arrivals}", f"task-late-{arrivals}")

    results: list[str] = []
    lookup = _probing_lookup(ledger_path, results, during=add_another_entry)
    runs = ledger.reconcile("repo", (), lookup, now=OBSERVED + timedelta(seconds=1))

    expected = {"task-a", *(f"task-late-{index}" for index in range(1, arrivals + 1))}
    surviving = {entry.task_id for entry in ledger.active_for("repo", now=OBSERVED + timedelta(seconds=2))}
    assert surviving == expected, "an entry recorded during network I/O was overwritten"
    assert {run.task_id for run in runs} == expected
    sources = {run.task_id: run.projection_status_source for run in runs}
    # The straggler that arrived during the final round had no lookup, so it
    # falls back to the conservative ledger projection instead of vanishing.
    assert sources[f"task-late-{arrivals}"] == "ledger"


def test_entry_removed_during_a_lookup_is_not_resurrected(tmp_path: Path) -> None:
    """A concurrent prune during network I/O must not be undone by the commit."""
    ledger_path = tmp_path / "ledger.json"
    ledger = FileAuditLedger(ledger_path)
    ledger.record_started(_entry("audit.security", "task-a"))

    def prune_underneath() -> None:
        if (ledger_path.parent / "pruned").exists():
            return
        (ledger_path.parent / "pruned").write_text("done", encoding="utf-8")
        _run_worker(_PRUNE_MATCHING_HEAD, str(ledger_path), "audit.security")

    results: list[str] = []
    lookup = _probing_lookup(ledger_path, results, during=prune_underneath)
    upstream: tuple[AuditRun, ...] = ()
    runs = ledger.reconcile("repo", upstream, lookup, now=OBSERVED + timedelta(seconds=1))

    assert ledger.active_for("repo", now=OBSERVED + timedelta(seconds=2)) == ()
    assert runs == ()
