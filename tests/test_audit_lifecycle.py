import pytest

from enji_guard_cli.audit.lifecycle import is_active_run, task_lifecycle
from enji_guard_cli.audit.ports import AuditRun


@pytest.mark.parametrize(
    ("status", "started_at", "completed_at", "expected"),
    [
        ("failed", None, None, "failed"),
        ("error", "started", None, "failed"),
        ("completed", "started", None, "completed"),
        ("running", None, None, "running"),
        ("in_progress", None, None, "running"),
        ("queued", None, None, "queued"),
        ("pending", None, None, "queued"),
        ("unknown", "started", None, "running"),
        ("unknown", None, None, "queued"),
        ("running", "started", "completed", "completed"),
        ("unknown", "started", "completed", "completed"),
    ],
)
def test_task_lifecycle_has_one_shared_precedence(
    status: str,
    started_at: str | None,
    completed_at: str | None,
    expected: str,
) -> None:
    assert task_lifecycle(status, started_at=started_at, completed_at=completed_at) == expected


@pytest.mark.parametrize("status", ["failed", "failure", "error", "completed", "canceled"])
def test_is_active_run_rejects_failure_and_terminal_aliases(status: str) -> None:
    assert not is_active_run(AuditRun("task", "audit.security", status, None, None, None))


def test_is_active_run_accepts_running_without_started_timestamp() -> None:
    assert is_active_run(AuditRun("task", "audit.security", "running", None, None, None))
