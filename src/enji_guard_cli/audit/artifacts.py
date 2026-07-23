"""Read completed audit artifacts without leaking gateway payload vocabulary."""

from dataclasses import dataclass
from typing import Literal, cast

from enji_guard_cli.audit.errors import AuditNotFoundError
from enji_guard_cli.audit.lifecycle import _timestamp, lifecycle_priority, task_lifecycle
from enji_guard_cli.audit.models import AuditCatalog
from enji_guard_cli.audit.ports import (
    AuditArtifact,
    AuditFreshness,
    AuditNewerRun,
    AuditReportRef,
    AuditRun,
    AuditStatusItem,
)


class AuditArtifactUnavailableError(AuditNotFoundError, ValueError):
    """Raised when a requested artifact cannot be read."""

    def __init__(self, audit_key: str, reason: str) -> None:
        self.audit_key = audit_key
        self.reason = reason
        super().__init__(f"{audit_key} artifact {reason}")


@dataclass(frozen=True, slots=True)
class ArtifactReadItem:
    audit_key: str
    available: bool
    artifact: AuditArtifact | None
    reason: str | None
    freshness: AuditFreshness
    newer_run: AuditNewerRun | None = None


@dataclass(frozen=True, slots=True)
class AuditRead:
    """Full report-content projection returned to delivery adapters."""

    repo_id: str
    audits: tuple[ArtifactReadItem, ...]


@dataclass(frozen=True, slots=True)
class AuditSummaryItem:
    """Compact audit metadata projection; report content intentionally excluded."""

    audit_key: str
    available: bool
    score: int | float | None
    generated_at: str | None
    reason: str | None
    freshness: AuditFreshness
    task_id: str | None = None
    completed_at: str | None = None
    collected_at: str | None = None
    newer_run: AuditNewerRun | None = None


@dataclass(frozen=True, slots=True)
class AuditSummary:
    repo_id: str
    audits: tuple[AuditSummaryItem, ...]


def summarize_artifacts(repo_id: str, items: tuple[ArtifactReadItem, ...]) -> AuditSummary:
    """Discard report bodies at the Audit boundary before delivery sees them."""

    return AuditSummary(
        repo_id,
        tuple(
            AuditSummaryItem(
                audit_key=item.audit_key,
                available=item.available,
                score=item.artifact.score if item.artifact is not None else None,
                generated_at=item.artifact.generated_at if item.artifact is not None else None,
                reason=item.reason,
                freshness=item.freshness,
                task_id=item.artifact.task_id if item.artifact is not None else None,
                completed_at=item.artifact.completed_at if item.artifact is not None else None,
                collected_at=item.artifact.collected_at if item.artifact is not None else None,
                newer_run=item.newer_run,
            )
            for item in items
        ),
    )


def select_artifacts(
    status: tuple[AuditStatusItem, ...],
    selectors: list[str],
    *,
    all_artifacts: bool,
    catalog: AuditCatalog,
) -> tuple[AuditStatusItem, ...]:
    """Resolve suffix selectors without using status as report-history authority."""

    if all_artifacts and selectors:
        raise ValueError("pass audit selectors or --all, not both")
    if all_artifacts:
        return tuple(status)
    if not selectors:
        return tuple(status)
    by_selector = {audit.selector: audit.action_key for audit in catalog.published_audits}
    by_key = {item.audit_key: item for item in status}
    selected: list[AuditStatusItem] = []
    for selector in selectors:
        audit_key = by_selector.get(selector)
        item = by_key.get(audit_key) if audit_key else None
        if item is None:
            raise AuditArtifactUnavailableError(selector, "status not found")
        selected.append(item)
    return tuple(selected)


def choose_report_ref(refs: tuple[AuditReportRef, ...]) -> AuditReportRef | None:
    """Choose the first usable report, retaining upstream newest-first order."""

    return next(
        (ref for ref in refs if ref.has_report and _nonblank(ref.task_id) and _nonblank(ref.completed_at)),
        None,
    )


def newer_run_for_report(
    ref: AuditReportRef,
    runs: tuple[AuditRun, ...],
    *,
    action_key: str | None = None,
    report_is_stale: bool = False,
) -> AuditNewerRun | None:
    """Return a matching active task that is newer than the selected report."""

    if not ref.task_id:
        return None
    completed = _parse_time(ref.completed_at)
    # Without a parseable report completion there is no trustworthy baseline
    # against which to prove that an active run is newer.
    if completed is None:
        return None
    candidates: list[tuple[tuple[float, int, str, str], AuditRun, str]] = []
    for run in runs:
        state = task_lifecycle(run.status, started_at=run.started_at, completed_at=run.completed_at)
        if run.task_id is None or state not in {"queued", "running"}:
            continue
        if action_key is not None and run.action_key != action_key:
            continue
        # Enji may reuse the completed report's task id while projecting a
        # newly queued run.  A stale report plus a matching active projection
        # is sufficient evidence that the displayed artifact is being
        # superseded, even when upstream supplies no new timestamp or id.
        if report_is_stale:
            candidates.append(
                (
                    (
                        _parse_time(run.started_at or run.created_at) or float("-inf"),
                        lifecycle_priority(state),
                        run.task_id,
                        run.status or "",
                    ),
                    run,
                    state,
                )
            )
            continue
        if run.task_id == ref.task_id:
            continue
        started = run.started_at or run.created_at
        started_epoch = _parse_time(started)
        if completed is not None and (started_epoch is None or started_epoch <= completed):
            continue
        candidates.append(
            (
                (
                    started_epoch if started_epoch is not None else float("-inf"),
                    lifecycle_priority(state),
                    run.task_id,
                    run.status or "",
                ),
                run,
                state,
            )
        )
    if not candidates:
        return None
    _, run, state = max(candidates, key=lambda candidate: candidate[0])
    return AuditNewerRun(
        run.task_id or "",
        run.status,
        run.created_at,
        run.started_at,
        cast(Literal["queued", "running"], state),
    )


def _nonblank(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_time(value: str | None) -> float | None:
    if not _nonblank(value):
        return None
    assert isinstance(value, str)
    try:
        return _timestamp(value.strip())
    except OSError, OverflowError, TypeError, ValueError:
        return None
