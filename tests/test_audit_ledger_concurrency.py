"""Concurrency acceptance for the durable started-run ledger.

The ledger is the guard against starting a second audit run for a repository
that already has one in flight, so a lost update means double-charging the
customer.  These exercises drive real threads and real interpreters against one
ledger file; no upstream service is involved.
"""

import os
import subprocess
import sys
import textwrap
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from enji_guard_cli.audit.ledger import FileAuditLedger, new_entry
from enji_guard_cli.audit.ports import AuditLedgerEntry, AuditRunStart, AuditTaskDetail

ROOT = Path(__file__).parents[1]
WORKER_COUNT = 4
ENTRIES_PER_WORKER = 40
PROCESS_TIMEOUT_SECONDS = 120.0

_WORKER = textwrap.dedent(
    """
    import sys
    import time
    from datetime import UTC, datetime
    from pathlib import Path

    from enji_guard_cli.audit.ledger import FileAuditLedger, new_entry
    from enji_guard_cli.audit.ports import AuditRunStart

    ledger_path = Path(sys.argv[1])
    worker = sys.argv[2]
    entries = int(sys.argv[3])
    ready_path = ledger_path.parent / f"ready-{worker}"
    start_path = ledger_path.parent / "start"

    ledger = FileAuditLedger(ledger_path)
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    ready_path.write_text("ready", encoding="utf-8")
    while not start_path.exists():
        time.sleep(0.001)
    for index in range(entries):
        ledger.record_started(
            new_entry(
                AuditRunStart(
                    "repo",
                    "project",
                    f"audit.{worker}.{index}",
                    f"task-{worker}-{index}",
                    "queued",
                    None,
                    None,
                ),
                observed_at=observed,
            )
        )
    """
)


def _entry(audit_key: str, task_id: str) -> AuditLedgerEntry:
    return new_entry(
        AuditRunStart("repo", "project", audit_key, task_id, "queued", None, None),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _recorded_task_ids(ledger: FileAuditLedger) -> set[str | None]:
    return {
        run.task_id
        for run in ledger.reconcile(
            "repo",
            (),
            lambda task_id: AuditTaskDetail(task_id, "queued"),
            now=datetime(2026, 1, 1, 1, tzinfo=UTC),
        )
    }


def test_concurrent_threads_never_lose_a_started_run(tmp_path: Path) -> None:
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    barrier = threading.Barrier(WORKER_COUNT)

    def record(worker: int) -> None:
        barrier.wait()
        for index in range(ENTRIES_PER_WORKER):
            ledger.record_started(_entry(f"audit.{worker}.{index}", f"task-{worker}-{index}"))

    threads = [threading.Thread(target=record, args=(worker,)) for worker in range(WORKER_COUNT)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    expected = {f"task-{worker}-{index}" for worker in range(WORKER_COUNT) for index in range(ENTRIES_PER_WORKER)}
    assert _recorded_task_ids(ledger) == expected


def test_concurrent_processes_never_lose_a_started_run(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(ledger_path), str(worker), str(ENTRIES_PER_WORKER)],
            cwd=ROOT,
            env=os.environ | {"PYTHONPATH": str(ROOT / "src")},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for worker in range(WORKER_COUNT)
    ]
    try:
        ready = [tmp_path / f"ready-{worker}" for worker in range(WORKER_COUNT)]
        while not all(path.exists() for path in ready):
            for worker in workers:
                assert worker.poll() is None, "ledger worker exited before the start gate"
            time.sleep(0.001)
        (tmp_path / "start").write_text("go", encoding="utf-8")
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=PROCESS_TIMEOUT_SECONDS)
            assert worker.returncode == 0, f"worker failed: {stdout}{stderr}"
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()

    expected = {f"task-{worker}-{index}" for worker in range(WORKER_COUNT) for index in range(ENTRIES_PER_WORKER)}
    assert _recorded_task_ids(FileAuditLedger(ledger_path)) == expected
