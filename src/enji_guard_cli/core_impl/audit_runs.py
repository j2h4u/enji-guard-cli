from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from enji_guard_cli.audits import AuditCatalog, AuditDefinition
from enji_guard_cli.core_impl.audit_tasks import (
    action_title,
    catalog_action,
    linked_web_resources,
    project_repo,
    repo_full_name,
    task_description,
)
from enji_guard_cli.core_impl.models import (
    DEFAULT_EXECUTION_FLOW,
    AuditRunBatchPayload,
    AuditRunBatchResultItem,
    AuditRunSkippedPayload,
    ReportAuditStatusPayload,
    ReportStatusPayload,
)
from enji_guard_cli.core_impl.payloads import json_object_or_default, json_str, required_str
from enji_guard_cli.core_impl.repo_status import (
    active_runs_for_action,
    current_head_sha,
    last_audited_head_sha,
    out_of_date,
)
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

type GetRepoRerunState = Callable[[str], JsonObjectPayload]
type StartAuditRun[TCreateRequest] = Callable[[TCreateRequest], JsonObjectPayload]
type MakeAuditRunCreate[TCreateRequest] = Callable[[str, str, str, JsonObjectPayload], TCreateRequest]
type ProjectDetail = Callable[[str], JsonObjectPayload]
type Runbook = Callable[[str], JsonObjectPayload]
type CurrentRepoActiveRuns = Callable[[str], list[JsonValue]]


@dataclass(frozen=True, slots=True)
class RecordStartedRunContext:
    repo_id: str
    project_id: str
    action_key: str
    response: JsonObjectPayload
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


@dataclass(frozen=True, slots=True)
class AuditRunTaskContext:
    project_id: str
    repo_id: str
    action_key: str
    project: JsonObjectPayload
    catalog: JsonObjectPayload


@dataclass(frozen=True, slots=True)
class StartReportAuditsContext:
    repo_id: str
    project_id: str
    audits: list[AuditDefinition]
    catalog_payload: JsonObjectPayload


def start_audit[TCreateRequest](
    repo_id: str,
    project_id: str,
    audit: AuditDefinition,
    catalog_payload: JsonObjectPayload,
    *,
    dependencies: StartAuditDependencies[TCreateRequest],
) -> JsonObjectPayload | AuditRunSkippedPayload:
    action_key = audit.action_key
    active_runs = active_runs_for_action(dependencies.current_repo_active_runs(repo_id), action_key)
    if active_runs:
        return skipped_audit_payload(action_key, action_key, active_runs)
    response = dependencies.start_audit_run(
        dependencies.make_audit_run_create(
            repo_id,
            project_id,
            action_key,
            audit_run_task_body(
                AuditRunTaskContext(
                    project_id=project_id,
                    repo_id=repo_id,
                    action_key=action_key,
                    project=dependencies.project_detail(project_id),
                    catalog=catalog_payload,
                ),
                runbook=dependencies.runbook,
            ),
        )
    )
    dependencies.record_started_run(
        RecordStartedRunContext(
            repo_id=repo_id,
            project_id=project_id,
            action_key=action_key,
            response=response,
            current_head_sha=None,
            last_audited_head_sha=None,
        )
    )
    return response


def start_report_audits_for_target[TCreateRequest](
    context: StartReportAuditsContext,
    *,
    dependencies: StartAuditDependencies[TCreateRequest],
    get_repo_rerun_state: GetRepoRerunState,
) -> AuditRunBatchPayload:
    result_matrix: list[AuditRunBatchResultItem] = []
    rerun_state = get_repo_rerun_state(context.repo_id)
    current_sha = current_head_sha(rerun_state)
    active_runs = dependencies.current_repo_active_runs(context.repo_id)
    project = dependencies.project_detail(context.project_id)
    for audit in context.audits:
        action_key = audit.action_key
        last_sha = last_audited_head_sha(rerun_state, action_key)
        matching_active_runs = active_runs_for_action(active_runs, action_key)
        if matching_active_runs:
            state = _active_run_state(matching_active_runs)
            task_id, task_status = _active_run_task(matching_active_runs[0])
            result_matrix.append(
                _batch_result_item(
                    audit.action_key,
                    action_key,
                    "already_running" if state == "running" else state,
                    (current_sha, last_sha),
                    (task_id, task_status),
                )
            )
            continue
        if out_of_date(current_sha, last_sha) is False:
            result_matrix.append(
                _batch_result_item(
                    audit.action_key,
                    action_key,
                    "up_to_date",
                    (current_sha, last_sha),
                )
            )
            continue
        try:
            response = dependencies.start_audit_run(
                dependencies.make_audit_run_create(
                    context.repo_id,
                    context.project_id,
                    action_key,
                    audit_run_task_body(
                        AuditRunTaskContext(
                            project_id=context.project_id,
                            repo_id=context.repo_id,
                            action_key=action_key,
                            project=project,
                            catalog=context.catalog_payload,
                        ),
                        runbook=dependencies.runbook,
                    ),
                )
            )
        except EnjiApiError:
            result_matrix.append(
                _batch_result_item(
                    audit.action_key,
                    action_key,
                    "failed",
                    (current_sha, last_sha),
                )
            )
            continue
        task_id, task_status = _start_task_identity(response)
        dependencies.record_started_run(
            RecordStartedRunContext(
                repo_id=context.repo_id,
                project_id=context.project_id,
                action_key=action_key,
                response=response,
                current_head_sha=current_sha,
                last_audited_head_sha=last_sha,
            )
        )
        result_matrix.append(
            _batch_result_item(
                audit.action_key,
                action_key,
                "started",
                (current_sha, last_sha),
                (task_id, task_status),
            )
        )
    return {"results": result_matrix}


def selected_audits(audits: list[str], *, all_reports: bool, catalog: AuditCatalog) -> list[AuditDefinition]:
    if all_reports:
        if audits:
            raise ValueError("pass audit selectors or --all, not both")
        return list(catalog.published_audits)
    if not audits:
        raise ValueError("pass at least one audit selector or --all")
    audits_by_selector = {audit.selector: audit for audit in catalog.published_audits}
    selected = [audits_by_selector.get(selector) for selector in audits]
    if missing := [selector for selector, audit in zip(audits, selected, strict=True) if audit is None]:
        raise ValueError(f"unknown audit selector: {missing[0]}")
    return [audit for audit in selected if audit is not None]


def linked_running_report_results(
    status: ReportStatusPayload,
    audits: list[AuditDefinition],
) -> dict[str, AuditRunBatchResultItem]:
    items_by_action = {item["action_key"]: item for item in status["items"]}
    results: dict[str, AuditRunBatchResultItem] = {}
    for audit in audits:
        action_key = audit.action_key
        item = items_by_action.get(action_key)
        if item is None or not has_running_report_link(item):
            continue
        report = item["report"]
        results[action_key] = {
            "audit": action_key,
            "action_key": action_key,
            "state": "already_running",
            "current_head_sha": report["current_head_sha"],
            "last_audited_head_sha": report["audited_head_sha"],
            "task_id": report["fleet_task_id"],
            "task_status": report["run_status"],
        }
    return results


def has_running_report_link(item: ReportAuditStatusPayload) -> bool:
    report = item["report"]
    task = item["task"]
    return (
        task["active"] is False
        and report["can_read"] is True
        and report["fleet_task_id"] is not None
        and report["audited_head_sha"] is None
        and report["completed_at"] is None
    )


def ordered_audit_results(
    audits: list[AuditDefinition],
    linked_results: dict[str, AuditRunBatchResultItem],
    started_results: list[AuditRunBatchResultItem],
) -> list[AuditRunBatchResultItem]:
    results_by_action = {result["action_key"]: result for result in started_results}
    results_by_action.update(linked_results)
    return [results_by_action[audit.action_key] for audit in audits]


def skipped_audit_payload(audit: str, action_key: str, active_runs: list[JsonValue]) -> AuditRunSkippedPayload:
    return {
        "skipped": True,
        "audit": audit,
        "action_key": action_key,
        "reason": "already_running",
        "active_runs": active_runs,
    }


def _active_run_state(active_runs: list[JsonValue]) -> Literal["queued", "running"]:
    for active_run in active_runs:
        if not isinstance(active_run, dict):
            continue
        started_at = json_str(active_run.get("startedAt"))
        if started_at is None:
            return "queued"
    return "running"


def _batch_result_item(
    audit: str,
    action_key: str,
    state: Literal["started", "queued", "already_running", "up_to_date", "failed"],
    head_hashes: tuple[str | None, str | None],
    task: tuple[str | None, str | None] = (None, None),
) -> AuditRunBatchResultItem:
    current_head_sha, last_audited_head_sha = head_hashes
    task_id, task_status = task
    item: AuditRunBatchResultItem = {
        "audit": audit,
        "action_key": action_key,
        "state": state,
        "current_head_sha": current_head_sha,
        "last_audited_head_sha": last_audited_head_sha,
    }
    if task_id is not None:
        item["task_id"] = task_id
    if task_status is not None:
        item["task_status"] = task_status
    return item


def _start_task_identity(response: JsonObjectPayload) -> tuple[str | None, str | None]:
    task = response.get("task")
    if isinstance(task, dict):
        task_id = json_str(task.get("id")) or json_str(task.get("fleetTaskId")) or json_str(task.get("taskId"))
        task_status = (
            json_str(task.get("status")) or json_str(task.get("lifecycle_state")) or json_str(task.get("state"))
        )
        return task_id, task_status
    if task is not None:
        return json_str(task), None
    task_id = json_str(response.get("id"))
    if task_id is None:
        task_id = json_str(response.get("taskId")) or json_str(response.get("fleetTaskId"))
    task_status = json_str(response.get("status")) or json_str(response.get("state"))
    return task_id, task_status


def _active_run_task(active_run: JsonValue) -> tuple[str | None, str | None]:
    if not isinstance(active_run, dict):
        return None, None
    task = active_run.get("task")
    if isinstance(task, dict):
        task_id = (
            json_str(task.get("fleetTaskId"))
            or json_str(task.get("id"))
            or json_str(active_run.get("fleetTaskId"))
            or json_str(active_run.get("taskId"))
        )
        task_status = (
            json_str(task.get("status"))
            or json_str(task.get("state"))
            or json_str(active_run.get("status"))
            or json_str(active_run.get("state"))
        )
        return task_id, task_status
    task_id = (
        json_str(active_run.get("fleetTaskId")) or json_str(active_run.get("taskId")) or json_str(active_run.get("id"))
    )
    task_status = json_str(active_run.get("status")) or json_str(active_run.get("state"))
    return task_id, task_status


def audit_run_task_body(
    context: AuditRunTaskContext,
    *,
    runbook: Runbook,
) -> JsonObjectPayload:
    repo = project_repo(context.project, context.repo_id)
    action = catalog_action(context.catalog, context.action_key)
    runbook_id = required_str(
        action,
        "fleetRunbookId",
        f"curated action {context.action_key} has no Fleet runbook",
    )
    runbook_payload = runbook(runbook_id)
    full_name = repo_full_name(repo)
    return {
        "title": f"{action_title(action)} for {full_name}",
        "description": task_description(action, repo, linked_web_resources(context.project, context.repo_id)),
        "project_id": context.project_id,
        "execution_flow": json_str(runbook_payload.get("suggested_flow")) or DEFAULT_EXECUTION_FLOW,
        "flow_config": json_object_or_default(runbook_payload.get("suggested_flow_config")),
        "runbook_id": runbook_id,
        "scope_type": "project",
        "scope_owner": context.project_id,
        "origin_type": "manual",
        "repo_access_contexts": [{"provider": "github", "repo_full_name": full_name}],
    }
