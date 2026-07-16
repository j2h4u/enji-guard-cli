"""Audit run application use-cases over neutral Audit projections."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from enji_guard_cli.audit import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import (
    AuditProject,
    AuditReportStatus,
    AuditRerunState,
    AuditRun,
    AuditRunbookMetadata,
    AuditTaskBody,
)
from enji_guard_cli.audit.tasks import AuditTaskContext, task_for_repo

type AuditRunBatchResultItem = dict[str, object]
type AuditRunBatchPayload = dict[str, object]
type AuditRunSkippedPayload = dict[str, object]
type GetRepoRerunState = Callable[[str], AuditRerunState]
type StartAuditRun[TCreateRequest] = Callable[[TCreateRequest], object]
type MakeAuditRunCreate[TCreateRequest] = Callable[[str, str, str, AuditTaskBody], TCreateRequest]
type ProjectDetail = Callable[[str], AuditProject]
type Runbook = Callable[[str], AuditRunbookMetadata]
type CurrentRepoActiveRuns = Callable[[str], tuple[AuditRun, ...]]
type TaskIdentity = Callable[[object], tuple[str | None, str | None]]


@dataclass(frozen=True, slots=True)
class RecordStartedRunContext:
    repo_id: str
    project_id: str
    action_key: str
    task_id: str | None
    task_status: str | None
    current_head_sha: str | None
    last_audited_head_sha: str | None


type RecordStartedRun = Callable[[RecordStartedRunContext], None]


@dataclass(frozen=True, slots=True)
class StartAuditDependencies[TCreateRequest]:
    make_audit_run_create: MakeAuditRunCreate[TCreateRequest]
    start_audit_run: StartAuditRun[TCreateRequest]
    project_detail: ProjectDetail
    runbook: Runbook
    current_repo_active_runs: CurrentRepoActiveRuns
    record_started_run: RecordStartedRun
    task_identity: TaskIdentity
    start_error: type[Exception] = Exception


@dataclass(frozen=True, slots=True)
class AuditRunTaskContext:
    project_id: str
    repo_id: str
    action_key: str
    project: AuditProject
    catalog: AuditCatalog


@dataclass(frozen=True, slots=True)
class StartReportAuditsContext:
    repo_id: str
    project_id: str
    audits: list[AuditDefinition]
    catalog: AuditCatalog


def start_audit[TCreateRequest](
    repo_id: str,
    project_id: str,
    audit: AuditDefinition,
    catalog: AuditCatalog,
    *,
    dependencies: StartAuditDependencies[TCreateRequest],
) -> object | AuditRunSkippedPayload:
    active_runs = active_runs_for_action(dependencies.current_repo_active_runs(repo_id), audit.action_key)
    if active_runs:
        return skipped_audit_payload(audit.action_key, audit.action_key, active_runs)
    response = dependencies.start_audit_run(
        dependencies.make_audit_run_create(
            repo_id,
            project_id,
            audit.action_key,
            audit_run_task_body(
                AuditRunTaskContext(
                    project_id, repo_id, audit.action_key, dependencies.project_detail(project_id), catalog
                ),
                runbook=dependencies.runbook,
            ),
        )
    )
    task_id, task_status = dependencies.task_identity(response)
    dependencies.record_started_run(
        RecordStartedRunContext(repo_id, project_id, audit.action_key, task_id, task_status, None, None)
    )
    return response


def start_report_audits_for_target[TCreateRequest](
    context: StartReportAuditsContext,
    *,
    dependencies: StartAuditDependencies[TCreateRequest],
    get_repo_rerun_state: GetRepoRerunState,
) -> AuditRunBatchPayload:
    results: list[AuditRunBatchResultItem] = []
    rerun_state = get_repo_rerun_state(context.repo_id)
    current_sha = rerun_state.current_head_sha
    active_runs = dependencies.current_repo_active_runs(context.repo_id)
    project = dependencies.project_detail(context.project_id)
    for audit in context.audits:
        action_key = audit.action_key
        last_sha = rerun_state.audited_head_shas.get(action_key)
        matching = active_runs_for_action(active_runs, action_key)
        if matching:
            task_id, task_status = _active_run_task(matching[0])
            state: Literal["queued", "already_running"] = (
                "queued" if matching[0].started_at is None else "already_running"
            )
            results.append(
                _batch_result_item(audit.action_key, action_key, state, (current_sha, last_sha), (task_id, task_status))
            )
            continue
        if out_of_date(current_sha, last_sha) is False:
            results.append(_batch_result_item(audit.action_key, action_key, "up_to_date", (current_sha, last_sha)))
            continue
        try:
            response = dependencies.start_audit_run(
                dependencies.make_audit_run_create(
                    context.repo_id,
                    context.project_id,
                    action_key,
                    audit_run_task_body(
                        AuditRunTaskContext(context.project_id, context.repo_id, action_key, project, context.catalog),
                        runbook=dependencies.runbook,
                    ),
                )
            )
        except dependencies.start_error:
            results.append(_batch_result_item(audit.action_key, action_key, "failed", (current_sha, last_sha)))
            continue
        task_id, task_status = dependencies.task_identity(response)
        dependencies.record_started_run(
            RecordStartedRunContext(
                context.repo_id, context.project_id, action_key, task_id, task_status, current_sha, last_sha
            )
        )
        results.append(
            _batch_result_item(audit.action_key, action_key, "started", (current_sha, last_sha), (task_id, task_status))
        )
    return {"results": results}


def selected_audits(audits: list[str], *, all_reports: bool, catalog: AuditCatalog) -> list[AuditDefinition]:
    if all_reports:
        if audits:
            raise ValueError("pass audit selectors or --all, not both")
        return list(catalog.published_audits)
    if not audits:
        raise ValueError("pass at least one audit selector or --all")
    by_selector = {audit.selector: audit for audit in catalog.published_audits}
    selected = [by_selector.get(selector) for selector in audits]
    if missing := [selector for selector, audit in zip(audits, selected, strict=True) if audit is None]:
        raise ValueError(f"unknown audit selector: {missing[0]}")
    return [audit for audit in selected if audit is not None]


def linked_running_report_results(
    status: tuple[AuditReportStatus, ...], audits: list[AuditDefinition]
) -> dict[str, AuditRunBatchResultItem]:
    items = {item.action_key: item for item in status}
    results: dict[str, AuditRunBatchResultItem] = {}
    for audit in audits:
        item = items.get(audit.action_key)
        if item is None or not has_running_report_link(item):
            continue
        results[audit.action_key] = {
            "audit": audit.action_key,
            "action_key": audit.action_key,
            "state": "already_running",
            "current_head_sha": item.current_head_sha,
            "last_audited_head_sha": item.audited_head_sha,
            "task_id": item.task_id,
            "task_status": item.task_status,
        }
    return results


def has_running_report_link(item: AuditReportStatus) -> bool:
    return (
        item.task_active is False
        and item.can_read
        and item.task_id is not None
        and item.audited_head_sha is None
        and item.completed_at is None
    )


def ordered_audit_results(
    audits: list[AuditDefinition],
    linked_results: dict[str, AuditRunBatchResultItem],
    started_results: list[AuditRunBatchResultItem],
) -> list[AuditRunBatchResultItem]:
    by_action = {result["action_key"]: result for result in started_results}
    by_action.update(linked_results)
    return [by_action[audit.action_key] for audit in audits]


def active_runs_for_action(active_runs: tuple[AuditRun, ...], action_key: str) -> tuple[AuditRun, ...]:
    return tuple(run for run in active_runs if run.action_key == action_key)


def out_of_date(current: str | None, audited: str | None) -> bool | None:
    return None if current is None or audited is None else current != audited


def skipped_audit_payload(audit: str, action_key: str, active_runs: tuple[AuditRun, ...]) -> AuditRunSkippedPayload:
    return {
        "skipped": True,
        "audit": audit,
        "action_key": action_key,
        "reason": "already_running",
        "active_runs": list(active_runs),
    }


def _active_run_task(run: AuditRun) -> tuple[str | None, str | None]:
    return run.task_id, run.status


def _batch_result_item(
    audit: str,
    action_key: str,
    state: Literal["started", "queued", "already_running", "up_to_date", "failed"],
    head_hashes: tuple[str | None, str | None],
    task: tuple[str | None, str | None] = (None, None),
) -> AuditRunBatchResultItem:
    current, audited = head_hashes
    task_id, task_status = task
    item: AuditRunBatchResultItem = {
        "audit": audit,
        "action_key": action_key,
        "state": state,
        "current_head_sha": current,
        "last_audited_head_sha": audited,
    }
    if task_id is not None:
        item["task_id"] = task_id
    if task_status is not None:
        item["task_status"] = task_status
    return item


def audit_run_task_body(context: AuditRunTaskContext, *, runbook: Runbook) -> AuditTaskBody:
    action = next(
        (
            item
            for item in (*context.catalog.published_audits, context.catalog.recon)
            if item.action_key == context.action_key
        ),
        None,
    )
    if action is None or not isinstance(action.runbook_id, str) or not action.runbook_id.strip():
        raise ValueError(f"catalog does not contain runbook for action key: {context.action_key}")
    return task_for_repo(
        AuditTaskContext(
            project=context.project,
            audit=action,
            runbook=runbook(action.runbook_id),
            runbook_id=action.runbook_id,
            artifact_schema_name=action.artifact_schema_name or "",
            artifact_schema_version=action.artifact_schema_version or "",
            description_template=action.task_description_template,
            repo_id=context.repo_id,
        ),
        context.repo_id,
    )
