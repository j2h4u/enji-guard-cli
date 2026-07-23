"""Build typed Audit status from gateway projections."""

from enji_guard_cli.audit.freshness import compare_heads
from enji_guard_cli.audit.lifecycle import active_runs_for_action, projection_sort_key, task_lifecycle
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import (
    AuditCurrentHeadStatus,
    AuditRerunState,
    AuditRun,
    AuditStatus,
    AuditStatusItem,
    AuditTaskLifecycle,
    AuditTaskLink,
)


def build_status(
    repo_id: str,
    catalog: AuditCatalog,
    links: tuple[AuditTaskLink, ...],
    active_runs: tuple[AuditRun, ...],
    rerun_state: AuditRerunState | None,
) -> AuditStatus:
    links_by_key = _links_by_action(links)
    runs_by_key = _runs_by_action(active_runs)
    current_sha = rerun_state.current_head_sha if rerun_state else None
    items = tuple(
        _status_item(
            audit, links_by_key.get(audit.action_key), runs_by_key.get(audit.action_key), current_sha, rerun_state
        )
        for audit in catalog.published_audits
    )
    return AuditStatus(repo_id=repo_id, current_head_sha=current_sha, items=items)


def _status_item(
    audit: AuditDefinition,
    link: AuditTaskLink | None,
    active_run: AuditRun | None,
    current_sha: str | None,
    rerun_state: AuditRerunState | None,
) -> AuditStatusItem:
    audited_sha = rerun_state.audited_head_shas.get(audit.action_key) if rerun_state else None
    run_lifecycle = (
        task_lifecycle(active_run.status, started_at=active_run.started_at, completed_at=active_run.completed_at)
        if active_run
        else "none"
    )
    link_lifecycle = (
        task_lifecycle(link.status, started_at=link.started_at, completed_at=link.completed_at) if link else "none"
    )
    # Active-run projections own task identity and fields whenever present.
    # Links without a run expose only terminal history; partial live links do
    # not invent activity when the active-runs endpoint is empty.
    if active_run is not None:
        source = active_run
        lifecycle = run_lifecycle
    elif link is not None and link_lifecycle in {"failed", "completed"}:
        source = link
        lifecycle = link_lifecycle
    else:
        source = None
        lifecycle = "none"
    run_status = source.status if source is not None else None
    freshness = compare_heads(current_sha, audited_sha)
    current_head = _current_head_status(freshness.state, current_sha, active_run, run_lifecycle)
    return AuditStatusItem(
        audit_key=audit.action_key,
        title=audit.title,
        freshness=freshness,
        can_read=_artifact_expected(audited_sha, lifecycle),
        task_lifecycle=lifecycle,
        task_id=source.task_id if source is not None else None,
        task_status=run_status,
        created_at=source.created_at if source is not None else None,
        started_at=source.started_at if source is not None else None,
        completed_at=source.completed_at if source is not None else None,
        current_head=current_head,
    )


def _current_head_status(
    freshness_state: str,
    current_sha: str | None,
    active_run: AuditRun | None,
    run_lifecycle: AuditTaskLifecycle,
) -> AuditCurrentHeadStatus:
    if freshness_state == "fresh":
        return AuditCurrentHeadStatus("ready", "none")
    if freshness_state == "unknown":
        return AuditCurrentHeadStatus("unknown", "resolve_unknown_head")
    if active_run is None:
        return AuditCurrentHeadStatus("missing", "start_current_head_run")
    return _active_current_head_status(current_sha, active_run, run_lifecycle)


def _active_current_head_status(
    current_sha: str | None,
    active_run: AuditRun,
    run_lifecycle: AuditTaskLifecycle,
) -> AuditCurrentHeadStatus:
    run_sha = active_run.current_head_sha
    if current_sha is not None and run_sha is not None and run_sha != current_sha:
        return AuditCurrentHeadStatus(
            "blocked",
            "start_current_head_run",
            stale_active_task_id=active_run.task_id,
            stale_active_current_head_sha=run_sha,
        )
    if run_lifecycle == "failed":
        return AuditCurrentHeadStatus(
            "failed",
            "inspect_failed_run",
            task_id=active_run.task_id,
            task_status=active_run.status,
            task_current_head_sha=run_sha,
        )
    if run_lifecycle == "queued":
        return AuditCurrentHeadStatus(
            "queued",
            "wait_for_current_head_run",
            task_id=active_run.task_id,
            task_status=active_run.status,
            task_current_head_sha=run_sha,
        )
    if run_lifecycle == "running":
        return AuditCurrentHeadStatus(
            "running",
            "wait_for_current_head_run",
            task_id=active_run.task_id,
            task_status=active_run.status,
            task_current_head_sha=run_sha,
        )
    return AuditCurrentHeadStatus("missing", "start_current_head_run")


def _artifact_expected(
    audited_sha: str | None,
    lifecycle: AuditTaskLifecycle,
) -> bool:
    """Use audit result state, not optional task history, as the read signal."""

    # Active work does not hide a previously completed report.  Report history
    # is resolved independently by the read workflow.
    return audited_sha is not None or lifecycle == "completed"


def _runs_by_action(runs: tuple[AuditRun, ...]) -> dict[str, AuditRun]:
    actions = {run.action_key for run in runs if run.action_key is not None}
    result: dict[str, AuditRun] = {}
    for action in actions:
        matching = active_runs_for_action(runs, action)
        if matching:
            result[action] = max(matching, key=projection_sort_key)
    return result


def _links_by_action(links: tuple[AuditTaskLink, ...]) -> dict[str, AuditTaskLink]:
    grouped: dict[str, list[AuditTaskLink]] = {}
    for link in links:
        if link.action_key is not None:
            grouped.setdefault(link.action_key, []).append(link)
    return {
        action: representative_link
        for action, items in grouped.items()
        if (representative_link := _representative_link(items)) is not None
    }


def _representative_link(links: list[AuditTaskLink]) -> AuditTaskLink | None:
    terminal = [
        link
        for link in links
        if task_lifecycle(link.status, started_at=link.started_at, completed_at=link.completed_at)
        in {"completed", "failed"}
    ]
    if not terminal:
        return None
    readable = [
        link
        for link in terminal
        if task_lifecycle(link.status, started_at=link.started_at, completed_at=link.completed_at) == "completed"
    ]
    return max(readable or terminal, key=projection_sort_key)
