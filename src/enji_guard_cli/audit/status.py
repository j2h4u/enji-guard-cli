"""Build typed Audit status from gateway projections."""

from enji_guard_cli.audit.freshness import compare_heads
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import (
    AuditItemStatus,
    AuditRerunState,
    AuditRun,
    AuditStatus,
    AuditStatusItem,
    AuditTaskLifecycle,
    AuditTaskLink,
)

TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled", "cancelled", "skipped"})


def build_status(
    repo_id: str,
    catalog: AuditCatalog,
    links: tuple[AuditTaskLink, ...],
    active_runs: tuple[AuditRun, ...],
    rerun_state: AuditRerunState | None,
) -> AuditStatus:
    links_by_key = {link.action_key: link for link in links if link.action_key is not None}
    runs_by_key = {run.action_key: run for run in active_runs if run.action_key is not None}
    current_sha = rerun_state.current_head_sha if rerun_state else None
    items = tuple(
        _status_item(
            audit, links_by_key.get(audit.action_key), runs_by_key.get(audit.action_key), current_sha, rerun_state
        )
        for audit in catalog.published_audits
    )
    return AuditStatus(repo_id=repo_id, current_head_sha=current_sha, items=items)


def status_items_from_projections(
    catalog: AuditCatalog,
    links: tuple[AuditTaskLink, ...],
    active_runs: tuple[AuditRun, ...],
    rerun_state: AuditRerunState | None,
) -> tuple[AuditStatusItem, ...]:
    return build_status("", catalog, links, active_runs, rerun_state).items


def audit_status_items(status: AuditStatus) -> tuple[AuditItemStatus, ...]:
    """Project typed status into the small DTO used by run/read use-cases."""

    return tuple(
        AuditItemStatus(
            action_key=item.audit_key,
            current_head_sha=item.freshness.current_head_sha,
            audited_head_sha=item.freshness.audited_head_sha,
            can_read=item.can_read,
            completed_at=item.completed_at,
            task_id=item.task_id,
            task_status=item.task_status,
            task_active=item.active,
        )
        for item in status.items
    )


def _status_item(
    audit: AuditDefinition,
    link: AuditTaskLink | None,
    active_run: AuditRun | None,
    current_sha: str | None,
    rerun_state: AuditRerunState | None,
) -> AuditStatusItem:
    audited_sha = rerun_state.audited_head_shas.get(audit.action_key) if rerun_state else None
    run_status = (link.status if link and link.status is not None else None) or (
        active_run.status if active_run else None
    )
    lifecycle = _lifecycle(active_run, active_run.status if active_run else run_status)
    return AuditStatusItem(
        audit_key=audit.action_key,
        title=audit.title,
        freshness=compare_heads(current_sha, audited_sha),
        can_read=_link_readable(link, active_run, lifecycle),
        task_lifecycle=lifecycle,
        task_id=(link.task_id if link else None) or (active_run.task_id if active_run else None),
        task_status=run_status,
        created_at=(link.created_at if link else None) or (active_run.created_at if active_run else None),
        started_at=(link.started_at if link else None) or (active_run.started_at if active_run else None),
        completed_at=(link.completed_at if link else None) or (active_run.completed_at if active_run else None),
    )


def _lifecycle(active_run: AuditRun | None, status: str | None) -> AuditTaskLifecycle:
    normalized = (status or "").strip().lower()
    if normalized == "failed" or (active_run is not None and normalized in {"error", "failure"}):
        return "failed"
    if normalized in TERMINAL_STATUSES:
        return "completed"
    if active_run is None:
        return "none"
    if active_run.completed_at is not None:
        return "completed"
    return "running" if active_run.started_at is not None else "queued"


def _link_readable(
    link: AuditTaskLink | None,
    active_run: AuditRun | None,
    lifecycle: AuditTaskLifecycle,
) -> bool:
    if link is None:
        return False
    # A task projection can lag the active-runs projection and omit its own
    # status.  An active queued/running task still makes the report unreadable.
    if active_run is not None and lifecycle in {"queued", "running"}:
        return False
    status = (link.status or "").strip().lower()
    return status not in {
        "queued",
        "running",
        "started",
        "failed",
        "error",
        "failure",
        "canceled",
        "cancelled",
        "skipped",
    }
