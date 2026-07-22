"""Audit task lifecycle predicates shared across application use-cases.

The upstream API exposes a handful of partially-populated projections.  Keep
the precedence rules in one place so status, duplicate guards, and ledger
reconciliation cannot disagree about whether a task is active.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from enji_guard_cli.audit.ports import AuditRun, AuditTaskLifecycle

FAILURE_STATUSES = frozenset({"failed", "failure", "error"})
TERMINAL_STATUSES = frozenset({"completed", "canceled", "cancelled", "skipped"}) | FAILURE_STATUSES
RUNNING_STATUSES = frozenset({"running", "started", "in_progress", "in-progress"})
QUEUED_STATUSES = frozenset({"queued", "pending"})


class _Projection(Protocol):
    @property
    def status(self) -> str | None: ...

    @property
    def started_at(self) -> str | None: ...

    @property
    def completed_at(self) -> str | None: ...

    @property
    def created_at(self) -> str | None: ...

    @property
    def task_id(self) -> str | None: ...


def is_terminal_status(status: str | None) -> bool:
    return (status or "").strip().lower() in TERMINAL_STATUSES


def task_lifecycle(
    status: str | None,
    *,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> AuditTaskLifecycle:
    """Return the canonical lifecycle for a projected task.

    Failure aliases take precedence over a completion timestamp.  A running
    status is authoritative even when the projection has not populated
    ``started_at`` yet.  Unknown states use the timestamp as the only useful
    signal and otherwise conservatively remain queued.
    """

    normalized = (status or "").strip().lower()
    if normalized in FAILURE_STATUSES:
        return "failed"
    if completed_at is not None or normalized in TERMINAL_STATUSES:
        return "completed"
    if normalized in RUNNING_STATUSES:
        return "running"
    if normalized in QUEUED_STATUSES:
        return "queued"
    return "running" if started_at is not None else "queued"


def is_active_run(run: AuditRun) -> bool:
    return task_lifecycle(run.status, started_at=run.started_at, completed_at=run.completed_at) in {
        "queued",
        "running",
    }


def active_runs_for_action(runs: Sequence[AuditRun], action_key: str) -> tuple[AuditRun, ...]:
    """Select the active subset shared by status and start duplicate guards."""

    return tuple(run for run in runs if run.action_key == action_key and is_active_run(run))


def lifecycle_priority(lifecycle: str) -> int:
    """Priority used when reducing conflicting projections for one action."""

    return {"running": 4, "queued": 3, "failed": 2, "completed": 1, "none": 0}.get(lifecycle, 0)


def projection_sort_key(projection: _Projection) -> tuple[int, float, *tuple[str, ...]]:
    """Rank projections consistently across status, start, and reconciliation."""

    lifecycle = task_lifecycle(
        projection.status,
        started_at=projection.started_at,
        completed_at=projection.completed_at,
    )
    timestamps = [
        _timestamp(value) for value in (projection.created_at, projection.started_at, projection.completed_at)
    ]
    newest = max((value for value in timestamps if value is not None), default=float("-inf"))
    # Keep the final choice stable even when duplicate projections have the
    # same task id, lifecycle, and timestamp but disagree on optional fields.
    # This makes reduction independent of the upstream response order.
    return (
        lifecycle_priority(lifecycle),
        newest,
        _projection_text(projection, "action_key"),
        projection.task_id or "",
        (projection.status or "").strip().lower(),
        projection.completed_at or "",
        projection.started_at or "",
        projection.created_at or "",
        _projection_text(projection, "projection_source"),
        _projection_text(projection, "projection_status_source"),
        _projection_text(projection, "expires_at"),
        _projection_text(projection, "current_head_sha"),
        _projection_text(projection, "last_audited_head_sha"),
        _projection_text(projection, "artifact_schema_name"),
    )


def representative_projection[ProjectionT: _Projection](projections: Sequence[ProjectionT]) -> ProjectionT:
    """Select a deterministic representative from duplicate projections."""

    return max(projections, key=projection_sort_key)


def _timestamp(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).timestamp()
    except OSError, OverflowError, TypeError, ValueError:
        return None


def _projection_text(projection: _Projection, name: str) -> str:
    value = getattr(projection, name, None)
    return value if isinstance(value, str) else ""
