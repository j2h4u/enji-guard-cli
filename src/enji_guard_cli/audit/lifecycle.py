"""Audit task lifecycle predicates shared across application use-cases."""

from enji_guard_cli.audit.ports import AuditRun

TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled", "cancelled", "skipped"})


def is_terminal_status(status: str | None) -> bool:
    return (status or "").strip().lower() in TERMINAL_STATUSES


def is_active_run(run: AuditRun) -> bool:
    return run.completed_at is None and not is_terminal_status(run.status)
