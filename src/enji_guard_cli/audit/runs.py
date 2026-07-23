"""Audit run application use-cases over neutral Audit projections."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from enji_guard_cli.audit import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.lifecycle import (
    active_runs_for_action,
    representative_projection,
    task_lifecycle,
)
from enji_guard_cli.audit.ports import (
    AuditProject,
    AuditRerunState,
    AuditRun,
    AuditRunbookMetadata,
    AuditRunStart,
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


type RecordAuditRunStart = Callable[[AuditRunStart], None]


@dataclass(frozen=True, slots=True)
class StartAuditDependencies[TCreateRequest]:
    make_audit_run_create: MakeAuditRunCreate[TCreateRequest]
    start_audit_run: StartAuditRun[TCreateRequest]
    project_detail: ProjectDetail
    runbook: Runbook
    current_repo_active_runs: CurrentRepoActiveRuns
    record_started_run: RecordAuditRunStart
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
class StartAuditsContext:
    repo_id: str
    project_id: str
    audits: list[AuditDefinition]
    catalog: AuditCatalog


@dataclass(frozen=True, slots=True)
class _StartOneState:
    rerun_state: AuditRerunState
    active_runs: tuple[AuditRun, ...]
    project: AuditProject


def start_audits_for_target[TCreateRequest](
    context: StartAuditsContext,
    *,
    dependencies: StartAuditDependencies[TCreateRequest],
    get_repo_rerun_state: GetRepoRerunState,
) -> AuditRunBatchPayload:
    rerun_state = get_repo_rerun_state(context.repo_id)
    active_runs = dependencies.current_repo_active_runs(context.repo_id)
    project = dependencies.project_detail(context.project_id)
    results = [
        _start_one_audit(
            audit,
            context=context,
            state=_StartOneState(rerun_state, active_runs, project),
            dependencies=dependencies,
        )
        for audit in context.audits
    ]
    return {"results": results}


def _start_one_audit[TCreateRequest](
    audit: AuditDefinition,
    *,
    context: StartAuditsContext,
    state: _StartOneState,
    dependencies: StartAuditDependencies[TCreateRequest],
) -> AuditRunBatchResultItem:
    action_key = audit.action_key
    current_sha = state.rerun_state.current_head_sha
    last_sha = state.rerun_state.audited_head_shas.get(action_key)
    matching = _current_head_active_runs(active_runs_for_action(state.active_runs, action_key), current_sha)
    if matching:
        representative = representative_projection(matching)
        task_id, task_status = _active_run_task(representative)
        run_state: Literal["queued", "already_running"] = (
            "already_running"
            if task_lifecycle(
                representative.status,
                started_at=representative.started_at,
                completed_at=representative.completed_at,
            )
            == "running"
            else "queued"
        )
        return _batch_result_item(action_key, action_key, run_state, (current_sha, last_sha), (task_id, task_status))
    if out_of_date(current_sha, last_sha) is False:
        return _batch_result_item(action_key, action_key, "up_to_date", (current_sha, last_sha))
    try:
        response = dependencies.start_audit_run(
            dependencies.make_audit_run_create(
                context.repo_id,
                context.project_id,
                action_key,
                audit_run_task_body(
                    AuditRunTaskContext(
                        context.project_id, context.repo_id, action_key, state.project, context.catalog
                    ),
                    runbook=dependencies.runbook,
                ),
            )
        )
    except dependencies.start_error:
        return _batch_result_item(action_key, action_key, "failed", (current_sha, last_sha))
    task_id, task_status = dependencies.task_identity(response)
    dependencies.record_started_run(
        AuditRunStart(context.repo_id, context.project_id, action_key, task_id, task_status, current_sha, last_sha)
    )
    return _batch_result_item(action_key, action_key, "started", (current_sha, last_sha), (task_id, task_status))


def out_of_date(current: str | None, audited: str | None) -> bool | None:
    return None if current is None or audited is None else current != audited


def _current_head_active_runs(active_runs: tuple[AuditRun, ...], current_sha: str | None) -> tuple[AuditRun, ...]:
    if current_sha is None:
        return active_runs
    return tuple(run for run in active_runs if run.current_head_sha in {None, current_sha})


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
