from collections.abc import Callable
from dataclasses import dataclass

from enji_guard_cli.audits import REPORT_AUDITS, AuditAlias
from enji_guard_cli.audits import require_report_audit as registry_require_report_audit
from enji_guard_cli.audits import resolve_audit as registry_resolve_audit
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
    AuditRunBatchItem,
    AuditRunBatchPayload,
    AuditRunSkippedItem,
    AuditRunSkippedPayload,
)
from enji_guard_cli.core_impl.payloads import json_object_or_default, json_str, required_str
from enji_guard_cli.core_impl.repo_status import (
    active_runs_for_action,
    current_active_runs,
    current_head_sha,
    last_audited_head_sha,
    out_of_date,
)
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

type ListRepoActiveRuns = Callable[[str], JsonObjectPayload]
type GetRepoRerunState = Callable[[str], JsonObjectPayload]
type StartAuditRun[TCreateRequest] = Callable[[TCreateRequest], JsonObjectPayload]
type MakeAuditRunCreate[TCreateRequest] = Callable[[str, str, str, JsonObjectPayload], TCreateRequest]
type ProjectDetail = Callable[[str], JsonObjectPayload]
type Catalog = Callable[[], JsonObjectPayload]
type Runbook = Callable[[str], JsonObjectPayload]


@dataclass(frozen=True, slots=True)
class StartAuditDependencies[TCreateRequest]:
    list_repo_active_runs: ListRepoActiveRuns
    make_audit_run_create: MakeAuditRunCreate[TCreateRequest]
    start_audit_run: StartAuditRun[TCreateRequest]
    project_detail: ProjectDetail
    catalog: Catalog
    runbook: Runbook


@dataclass(frozen=True, slots=True)
class AuditRunTaskContext:
    project_id: str
    repo_id: str
    action_key: str
    project: JsonObjectPayload
    catalog: JsonObjectPayload


def start_audit[TCreateRequest](
    repo_id: str,
    project_id: str,
    audit: AuditAlias,
    *,
    dependencies: StartAuditDependencies[TCreateRequest],
) -> JsonObjectPayload | AuditRunSkippedPayload:
    resolved = registry_resolve_audit(audit)
    action_key = resolved.action_key
    active_runs = active_runs_for_action(
        current_active_runs(dependencies.list_repo_active_runs(repo_id)),
        action_key,
    )
    if active_runs:
        return skipped_audit_payload(audit.value, action_key, active_runs)
    return dependencies.start_audit_run(
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
                    catalog=dependencies.catalog(),
                ),
                runbook=dependencies.runbook,
            ),
        )
    )


def start_report_audits_for_target[TCreateRequest](
    repo_id: str,
    project_id: str,
    audits: list[AuditAlias],
    *,
    dependencies: StartAuditDependencies[TCreateRequest],
    get_repo_rerun_state: GetRepoRerunState,
) -> AuditRunBatchPayload:
    runs: list[AuditRunBatchItem] = []
    skipped: list[AuditRunSkippedItem] = []
    active_runs = current_active_runs(dependencies.list_repo_active_runs(repo_id))
    rerun_state = get_repo_rerun_state(repo_id)
    current_sha = current_head_sha(rerun_state)
    project = dependencies.project_detail(project_id)
    catalog = dependencies.catalog()
    for alias in audits:
        audit = registry_require_report_audit(alias)
        action_key = audit.action_key
        last_sha = last_audited_head_sha(rerun_state, action_key)
        matching_active_runs = active_runs_for_action(active_runs, action_key)
        if matching_active_runs:
            skipped.append(
                {
                    "audit": alias.value,
                    "action_key": action_key,
                    "reason": "already_running",
                    "active_runs": matching_active_runs,
                    "current_head_sha": current_sha,
                    "last_audited_head_sha": last_sha,
                }
            )
            continue
        if out_of_date(current_sha, last_sha) is False:
            skipped.append(
                {
                    "audit": alias.value,
                    "action_key": action_key,
                    "reason": "up_to_date",
                    "active_runs": [],
                    "current_head_sha": current_sha,
                    "last_audited_head_sha": last_sha,
                }
            )
            continue
        runs.append(
            {
                "audit": alias.value,
                "action_key": action_key,
                "response": dependencies.start_audit_run(
                    dependencies.make_audit_run_create(
                        repo_id,
                        project_id,
                        action_key,
                        audit_run_task_body(
                            AuditRunTaskContext(
                                project_id=project_id,
                                repo_id=repo_id,
                                action_key=action_key,
                                project=project,
                                catalog=catalog,
                            ),
                            runbook=dependencies.runbook,
                        ),
                    )
                ),
            }
        )
    return {"runs": runs, "skipped": skipped}


def selected_report_audits(audits: list[AuditAlias], *, all_reports: bool) -> list[AuditAlias]:
    if all_reports:
        if audits:
            raise ValueError("pass report audits or --all, not both")
        return [audit.alias for audit in REPORT_AUDITS]
    if not audits:
        raise ValueError("pass at least one report audit or --all")
    for audit in audits:
        registry_require_report_audit(audit)
    return audits


def skipped_audit_payload(audit: str, action_key: str, active_runs: list[JsonValue]) -> AuditRunSkippedPayload:
    return {
        "skipped": True,
        "audit": audit,
        "action_key": action_key,
        "reason": "already_running",
        "active_runs": active_runs,
    }


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
