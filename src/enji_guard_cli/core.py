import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Never

from enji_guard_cli.audits import REPORT_AUDITS, AuditAlias, ReportAuditDefinition
from enji_guard_cli.audits import require_report_audit as registry_require_report_audit
from enji_guard_cli.audits import resolve_audit as registry_resolve_audit
from enji_guard_cli.auth import AuthError as AuthError
from enji_guard_cli.auth import AuthRefreshPayload as AuthRefreshPayload
from enji_guard_cli.auth import AuthStatusPayload as AuthStatusPayload
from enji_guard_cli.auth import ImportCredentialPayload as ImportCredentialPayload
from enji_guard_cli.auth import import_bearer_token as import_bearer_token
from enji_guard_cli.auth import import_cookie as import_cookie
from enji_guard_cli.auth import refresh_auth as refresh_auth
from enji_guard_cli.core_impl import report_reads as _report_reads
from enji_guard_cli.core_impl.audit_tasks import action_title as _action_title
from enji_guard_cli.core_impl.audit_tasks import catalog_action as _catalog_action
from enji_guard_cli.core_impl.audit_tasks import linked_web_resources as _linked_web_resources
from enji_guard_cli.core_impl.audit_tasks import project_repo as _project_repo
from enji_guard_cli.core_impl.audit_tasks import repo_full_name as _repo_full_name
from enji_guard_cli.core_impl.audit_tasks import task_description as _task_description
from enji_guard_cli.core_impl.email_preferences import email_preference_row as _email_preference_row
from enji_guard_cli.core_impl.email_preferences import email_preferences_patch as _email_preferences_patch
from enji_guard_cli.core_impl.email_preferences import email_preferences_payload as _email_preferences_payload
from enji_guard_cli.core_impl.models import (
    DEFAULT_EXECUTION_FLOW,
    DEFAULT_REPO_SORT,
    AuditRunBatchItem,
    AuditRunBatchPayload,
    AuditRunSkippedItem,
    AuditRunSkippedPayload,
    EmailPreferenceUpdate,
    ProjectRef,
    ProjectRuntimeStatusPayload,
    RepoResolvePayload,
    ReportStatusPayload,
    ReportWaitCallback,
    ReportWaitOptions,
    ReportWaitPayload,
    RepoRuntimeStatusPayload,
    RepoSort,
    RepoStatusAllPayload,
    RepoStatusSummaryPayload,
    RepoTargetPayload,
    ScheduleSettingsUpdate,
)
from enji_guard_cli.core_impl.models import (
    DEFAULT_REPORT_WAIT_HEARTBEAT_SECONDS as DEFAULT_REPORT_WAIT_HEARTBEAT_SECONDS,
)
from enji_guard_cli.core_impl.models import (
    DEFAULT_REPORT_WAIT_POLL_SECONDS as DEFAULT_REPORT_WAIT_POLL_SECONDS,
)
from enji_guard_cli.core_impl.models import (
    DEFAULT_REPORT_WAIT_TIMEOUT_SECONDS as DEFAULT_REPORT_WAIT_TIMEOUT_SECONDS,
)
from enji_guard_cli.core_impl.models import OperationName as OperationName
from enji_guard_cli.core_impl.models import OperationPayload as OperationPayload
from enji_guard_cli.core_impl.models import (
    OperationResult as OperationResult,
)
from enji_guard_cli.core_impl.models import OperationSpec as OperationSpec
from enji_guard_cli.core_impl.models import ReportAuditState as ReportAuditState
from enji_guard_cli.core_impl.operations import READ_OPERATION_SPECS as READ_OPERATION_SPECS
from enji_guard_cli.core_impl.operations import access_async_operation as access_async_operation
from enji_guard_cli.core_impl.operations import auth_status_async_operation as auth_status_async_operation
from enji_guard_cli.core_impl.operations import operation_catalog as operation_catalog
from enji_guard_cli.core_impl.operations import operation_payload as operation_payload
from enji_guard_cli.core_impl.operations import package_version as package_version
from enji_guard_cli.core_impl.operations import reports_list_async_operation as reports_list_async_operation
from enji_guard_cli.core_impl.operations import resolve_operation as resolve_operation
from enji_guard_cli.core_impl.operations import resolve_operation_result as resolve_operation_result
from enji_guard_cli.core_impl.operations import resolve_operation_spec as resolve_operation_spec
from enji_guard_cli.core_impl.payloads import json_dict as _json_dict
from enji_guard_cli.core_impl.payloads import json_object_list as _json_object_list
from enji_guard_cli.core_impl.payloads import json_object_or_default as _json_object_or_default
from enji_guard_cli.core_impl.payloads import json_object_payload as _json_object_payload
from enji_guard_cli.core_impl.payloads import json_str as _json_str
from enji_guard_cli.core_impl.payloads import required_str as _required_str
from enji_guard_cli.core_impl.project_admin import MoveRepoDependencies as _MoveRepoDependencies
from enji_guard_cli.core_impl.project_admin import connect_repo_payload as _connect_repo_payload
from enji_guard_cli.core_impl.project_admin import create_project_payload as _create_project_payload
from enji_guard_cli.core_impl.project_admin import delete_project_payload as _delete_project_payload
from enji_guard_cli.core_impl.project_admin import move_repo_payload as _move_repo_payload
from enji_guard_cli.core_impl.project_admin import rename_project_payload as _rename_project_payload
from enji_guard_cli.core_impl.repo_status import active_runs_for_action as _active_runs_for_action
from enji_guard_cli.core_impl.repo_status import current_active_runs as _current_active_runs
from enji_guard_cli.core_impl.repo_status import current_head_sha as _current_head_sha
from enji_guard_cli.core_impl.repo_status import empty_report_status as _empty_report_status
from enji_guard_cli.core_impl.repo_status import last_audited_head_sha as _last_audited_head_sha
from enji_guard_cli.core_impl.repo_status import next_poll_sleep as _next_poll_sleep
from enji_guard_cli.core_impl.repo_status import out_of_date as _out_of_date
from enji_guard_cli.core_impl.repo_status import report_status_from_task_links as _report_status_from_task_links
from enji_guard_cli.core_impl.repo_status import report_wait_payload as _report_wait_payload
from enji_guard_cli.core_impl.repo_status import sort_project_repos as _sort_project_repos
from enji_guard_cli.core_impl.repo_status import validate_report_wait_options as _validate_report_wait_options
from enji_guard_cli.core_impl.schedules import schedule_effective_state as _schedule_effective_state
from enji_guard_cli.core_impl.schedules import schedule_job_by_kind as _schedule_job_by_kind
from enji_guard_cli.core_impl.schedules import schedule_setting_row as _schedule_setting_row
from enji_guard_cli.core_impl.schedules import schedule_settings_payload as _schedule_settings_payload
from enji_guard_cli.core_impl.schedules import schedule_settings_payload_for_job as _schedule_settings_payload_for_job
from enji_guard_cli.core_impl.schedules import validate_schedule_settings_update as _validate_schedule_settings_update
from enji_guard_cli.core_impl.selectors import parse_github_repo as _parse_github_repo
from enji_guard_cli.core_impl.selectors import repo_target as _repo_target
from enji_guard_cli.core_impl.selectors import targeted_run_payload as _targeted_run_payload
from enji_guard_cli.core_impl.selectors import transfer_schedule_replacements as _transfer_schedule_replacements
from enji_guard_cli.core_impl.selectors import validate_write_scope as _validate_write_scope
from enji_guard_cli.core_impl.selectors import validated_project_name as _validated_project_name
from enji_guard_cli.core_impl.targets import matching_repo_targets as _matching_repo_targets_impl
from enji_guard_cli.core_impl.targets import project_refs as _project_refs_impl
from enji_guard_cli.core_impl.targets import project_repo_targets as _project_repo_targets_impl
from enji_guard_cli.core_impl.targets import resolve_single_project_id as _resolve_single_project_id_impl
from enji_guard_cli.core_impl.targets import resolve_single_repo_target as _resolve_single_repo_target_impl
from enji_guard_cli.core_impl.targets import selected_project_ids as _selected_project_ids_impl
from enji_guard_cli.core_impl.targets import selected_repo_targets as _selected_repo_targets_impl
from enji_guard_cli.enji_api import REPORTS_LIST_DEFAULT_SELECTOR as REPORTS_LIST_DEFAULT_SELECTOR
from enji_guard_cli.enji_api import (
    AuditRunCreate,
    RepoTransfer,
)
from enji_guard_cli.enji_api import _connect_project_repo as run_connect_project_repo
from enji_guard_cli.enji_api import audit_email_preferences as run_audit_email_preferences
from enji_guard_cli.enji_api import audit_summary_snapshot as run_audit_summary_snapshot
from enji_guard_cli.enji_api import catalog as run_catalog
from enji_guard_cli.enji_api import create_project as run_create_project
from enji_guard_cli.enji_api import delete_project as run_delete_project
from enji_guard_cli.enji_api import improvement_jobs as run_improvement_jobs
from enji_guard_cli.enji_api import move_repo as run_move_repo
from enji_guard_cli.enji_api import preflight_repo_move as run_preflight_repo_move
from enji_guard_cli.enji_api import project_detail as run_project_detail
from enji_guard_cli.enji_api import projects as run_projects
from enji_guard_cli.enji_api import put_audit_email_preferences as run_put_audit_email_preferences
from enji_guard_cli.enji_api import put_improvement_job as run_put_improvement_job
from enji_guard_cli.enji_api import rename_project as run_rename_project
from enji_guard_cli.enji_api import repo_active_runs as run_repo_active_runs
from enji_guard_cli.enji_api import repo_audit_rerun_state as run_repo_audit_rerun_state
from enji_guard_cli.enji_api import repo_task_links as run_repo_task_links
from enji_guard_cli.enji_api import runbook as run_runbook
from enji_guard_cli.enji_api import start_audit_run as run_start_audit_run
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue


def list_projects() -> JsonObjectPayload:
    return run_projects()


def create_project(name: str) -> JsonObjectPayload:
    return _create_project_payload(
        name,
        validate_project_name=_validated_project_name,
        create_project=run_create_project,
    )


def rename_project(project: str, name: str) -> JsonObjectPayload:
    return _rename_project_payload(
        project,
        name,
        resolve_single_project_id=_resolve_single_project_id,
        validate_project_name=_validated_project_name,
        rename_project=run_rename_project,
    )


def delete_project(project: str) -> JsonObjectPayload:
    return _delete_project_payload(
        project,
        resolve_single_project_id=_resolve_single_project_id,
        delete_project=run_delete_project,
    )


def list_project_inventory(project: str | None, sort: RepoSort = DEFAULT_REPO_SORT) -> RepoStatusAllPayload:
    project_ids = _selected_project_ids(project)
    project_status = _project_runtime_status if sort == "latest-report" else _project_inventory_status
    projects = [project_status(project_id) for project_id in project_ids]
    _sort_project_repos(projects, sort)
    return _repo_status_all_payload(projects)


def connect_repo(github_repo: str, project: str | None) -> JsonObjectPayload:
    return _connect_repo_payload(
        github_repo,
        project,
        resolve_single_project_id=_resolve_single_project_id,
        parse_github_repo=_parse_github_repo,
        connect_project_repo=run_connect_project_repo,
    )


def move_repo(repo: str, source_project: str | None, target_project: str) -> JsonObjectPayload:
    return _move_repo_payload(
        repo,
        source_project,
        target_project,
        dependencies=_MoveRepoDependencies(
            resolve_single_repo_target=_resolve_single_repo_target,
            resolve_single_project_id=_resolve_single_project_id,
            preflight_repo_move=run_preflight_repo_move,
            transfer_schedule_replacements=_transfer_schedule_replacements,
            make_repo_transfer=RepoTransfer,
            move_repo=run_move_repo,
        ),
    )


def _list_repo_active_runs(repo_id: str) -> JsonObjectPayload:
    return run_repo_active_runs(repo_id)


def _get_repo_rerun_state(repo_id: str) -> JsonObjectPayload:
    return run_repo_audit_rerun_state(repo_id)


def _list_repo_task_links(repo_id: str) -> JsonObjectPayload:
    return run_repo_task_links(repo_id)


def _report_status(repo_id: str) -> ReportStatusPayload:
    active_runs = _current_active_runs(_list_repo_active_runs(repo_id))
    rerun_state = _get_repo_rerun_state(repo_id)
    return _report_status_from_task_links(repo_id, _list_repo_task_links(repo_id), active_runs, rerun_state)


def repo_status_all(project_id: str | None, sort: RepoSort = DEFAULT_REPO_SORT) -> RepoStatusAllPayload:
    projects = [
        _project_runtime_status(selected_project_id) for selected_project_id in _selected_project_ids(project_id)
    ]
    _sort_project_repos(projects, sort)
    return _repo_status_all_payload(projects)


def runtime_status(repo: str | None, project: str | None, sort: RepoSort = DEFAULT_REPO_SORT) -> RepoStatusAllPayload:
    if repo is None:
        return repo_status_all(project, sort)

    projects = _project_statuses_for_repo(repo, project)
    if not any(project_status["repos"] for project_status in projects):
        _raise_bad_selector(f"repo selector matched no repos: {repo}")
    _sort_project_repos(projects, sort)
    return _repo_status_all_payload(projects)


def resolve_repo(repo: str, project: str | None) -> RepoResolvePayload:
    matches = _matching_repo_targets(repo, _selected_project_ids(project))
    if not matches:
        _raise_bad_selector(f"repo selector matched no repos: {repo}")
    return {"selector": repo, "resolved": len(matches) == 1, "matches": matches}


def wait_for_report_completion(
    repo_id: str,
    *,
    options: ReportWaitOptions,
    heartbeat: Callable[[ReportWaitPayload], None] | None,
) -> ReportWaitPayload:
    _validate_report_wait_options(options)
    started_at = time.monotonic()
    deadline = started_at + options.timeout_seconds
    next_heartbeat_at = started_at
    while True:
        status = _report_status(repo_id)
        payload = _report_wait_payload(repo_id, status, started_at, timed_out=False)
        if payload["complete"] or payload["reason"] == "failed":
            return payload
        now = time.monotonic()
        if now >= deadline:
            return _report_wait_payload(repo_id, status, started_at, timed_out=True)
        if heartbeat is not None and now >= next_heartbeat_at:
            heartbeat(payload)
            next_heartbeat_at += options.heartbeat_seconds
        time.sleep(_next_poll_sleep(deadline, options.poll_seconds))


def start_audit(
    repo_id: str,
    project_id: str,
    audit: AuditAlias,
) -> JsonObjectPayload | AuditRunSkippedPayload:
    resolved = registry_resolve_audit(audit)
    action_key = resolved.action_key
    active_runs = _active_runs_for_action(_current_active_runs(_list_repo_active_runs(repo_id)), action_key)
    if active_runs:
        return {
            "skipped": True,
            "audit": audit.value,
            "action_key": action_key,
            "reason": "already_running",
            "active_runs": active_runs,
        }
    return run_start_audit_run(
        AuditRunCreate(
            repo_id=repo_id,
            project_id=project_id,
            action_key=action_key,
            fleet_task_body=_audit_run_task_body(project_id, repo_id, action_key),
        )
    )


def start_recon(repo: str, project: str | None) -> dict[str, object]:
    target = _resolve_single_repo_target(repo, project)
    return _targeted_run_payload(target, start_audit(target["repo_id"], target["project_id"], AuditAlias.RECON))


def start_report_audits(
    repo: str,
    project: str | None,
    audits: list[AuditAlias],
    *,
    all_reports: bool,
) -> dict[str, object]:
    target = _resolve_single_repo_target(repo, project)
    selected_audits = _selected_report_audits(audits, all_reports=all_reports)
    return _targeted_run_payload(
        target,
        _start_report_audits_for_target(target["repo_id"], target["project_id"], selected_audits),
    )


def _start_report_audits_for_target(
    repo_id: str,
    project_id: str,
    audits: list[AuditAlias],
) -> AuditRunBatchPayload:
    runs: list[AuditRunBatchItem] = []
    skipped: list[AuditRunSkippedItem] = []
    active_runs = _current_active_runs(_list_repo_active_runs(repo_id))
    rerun_state = _get_repo_rerun_state(repo_id)
    current_head_sha = _current_head_sha(rerun_state)
    project = run_project_detail(project_id)
    catalog = run_catalog()
    for alias in audits:
        audit = registry_require_report_audit(alias)
        action_key = audit.action_key
        last_audited_head_sha = _last_audited_head_sha(rerun_state, action_key)
        matching_active_runs = _active_runs_for_action(active_runs, action_key)
        if matching_active_runs:
            skipped.append(
                {
                    "audit": audit.alias.value,
                    "action_key": action_key,
                    "reason": "already_running",
                    "active_runs": matching_active_runs,
                    "current_head_sha": current_head_sha,
                    "last_audited_head_sha": last_audited_head_sha,
                }
            )
            continue
        if _out_of_date(current_head_sha, last_audited_head_sha) is False:
            skipped.append(
                {
                    "audit": audit.alias.value,
                    "action_key": action_key,
                    "reason": "up_to_date",
                    "active_runs": [],
                    "current_head_sha": current_head_sha,
                    "last_audited_head_sha": last_audited_head_sha,
                }
            )
            continue
        runs.append(
            {
                "audit": audit.alias.value,
                "action_key": action_key,
                "response": run_start_audit_run(
                    AuditRunCreate(
                        repo_id=repo_id,
                        project_id=project_id,
                        action_key=action_key,
                        fleet_task_body=_audit_run_task_body_from_context(
                            project_id, repo_id, action_key, project, catalog
                        ),
                    )
                ),
            }
        )
    return {"runs": runs, "skipped": skipped}


def read_reports_for_repo(
    repo: str,
    project: str | None,
    audits: list[AuditAlias],
    *,
    all_reports: bool,
) -> dict[str, object]:
    target = _resolve_single_repo_target(repo, project)
    status = _report_status(target["repo_id"])
    selected_reports = _report_reads.selected_reports_to_read(status, audits, all_reports=all_reports)
    return _targeted_run_payload(
        target,
        _report_reads.read_reports_for_target(
            target["repo_id"],
            selected_reports,
            snapshot_reader=_read_report_snapshot,
            tolerate_unavailable=not audits,
        ),
    )


def _read_report_snapshot(repo_id: str, audit: AuditAlias) -> JsonObjectPayload:
    return run_audit_summary_snapshot(repo_id, registry_require_report_audit(audit).route_slug)


def list_email_preferences(repo: str | None, project: str | None) -> JsonObjectPayload:
    return _email_preferences_payload(
        [
            _email_preference_row(target, audit, _get_audit_email_preferences(target["repo_id"], audit.action_key))
            for target in _selected_repo_targets(repo, project)
            for audit in REPORT_AUDITS
        ]
    )


def _get_audit_email_preferences(repo_id: str, action_key: str) -> JsonObjectPayload:
    return run_audit_email_preferences(repo_id, action_key)


def set_email_preferences(
    repo: str | None,
    project: str | None,
    update: EmailPreferenceUpdate,
    *,
    all_repos: bool = False,
    all_projects: bool = False,
) -> JsonObjectPayload:
    patch = _email_preferences_patch(update)
    return _email_preferences_payload(
        [
            _email_preference_row(
                target,
                audit,
                run_put_audit_email_preferences(target["repo_id"], audit.action_key, patch),
            )
            for target in _selected_write_repo_targets(
                repo,
                project,
                all_repos=all_repos,
                all_projects=all_projects,
                operation="email set",
            )
            for audit in REPORT_AUDITS
        ]
    )


def _list_schedules(repo_id: str) -> JsonObjectPayload:
    return run_improvement_jobs(repo_id)


def list_schedule_settings(repo: str | None, project: str | None) -> JsonObjectPayload:
    rows = [
        _schedule_setting_row(target, audit, _schedule_job_by_kind(jobs, audit.job_kind))
        for target in _selected_repo_targets(repo, project)
        for jobs in (_list_schedules(target["repo_id"]),)
        for audit in REPORT_AUDITS
    ]
    return _schedule_settings_payload(rows)


def _set_schedule(
    repo_id: str,
    audit: AuditAlias,
    payload: object,
) -> JsonObjectPayload:
    return run_put_improvement_job(
        repo_id, registry_require_report_audit(audit).job_kind, _json_object_payload(payload)
    )


def set_schedule_settings(
    repo: str | None,
    project: str | None,
    update: ScheduleSettingsUpdate,
    *,
    all_repos: bool = False,
    all_projects: bool = False,
) -> JsonObjectPayload:
    _validate_schedule_settings_update(update)
    rows = [
        _set_schedule_setting(target, audit, jobs, update)
        for target in _selected_write_repo_targets(
            repo,
            project,
            all_repos=all_repos,
            all_projects=all_projects,
            operation="schedule set",
        )
        for jobs in (_list_schedules(target["repo_id"]),)
        for audit in REPORT_AUDITS
    ]
    return _schedule_settings_payload(rows)


def wait_for_reports(
    repo: str,
    project: str | None,
    *,
    options: ReportWaitOptions,
    heartbeat: ReportWaitCallback | None,
) -> dict[str, object]:
    target = _resolve_single_repo_target(repo, project)
    targeted_heartbeat: Callable[[ReportWaitPayload], None] | None = None
    if heartbeat is not None:

        def target_heartbeat(payload: ReportWaitPayload) -> None:
            heartbeat(_targeted_run_payload(target, payload))

        targeted_heartbeat = target_heartbeat

    payload = wait_for_report_completion(
        target["repo_id"],
        options=options,
        heartbeat=targeted_heartbeat,
    )
    return _targeted_run_payload(target, payload)


def _selected_project_ids(project: str | None) -> list[str]:
    return _selected_project_ids_impl(project, list_projects=list_projects, raise_bad_selector=_raise_bad_selector)


def _resolve_single_project_id(project: str | None) -> str:
    return _resolve_single_project_id_impl(project, list_projects=list_projects, raise_bad_selector=_raise_bad_selector)


def _project_refs() -> list[ProjectRef]:
    return _project_refs_impl(list_projects())


def _selected_repo_targets(repo: str | None, project: str | None) -> list[RepoTargetPayload]:
    return _selected_repo_targets_impl(
        repo,
        project,
        list_projects=list_projects,
        project_detail=run_project_detail,
        raise_bad_selector=_raise_bad_selector,
    )


def _selected_write_repo_targets(
    repo: str | None,
    project: str | None,
    *,
    all_repos: bool,
    all_projects: bool,
    operation: str,
) -> list[RepoTargetPayload]:
    _validate_write_scope(repo, project, all_repos=all_repos, all_projects=all_projects, operation=operation)
    if all_projects:
        return _selected_repo_targets(None, None)
    if all_repos:
        return _selected_repo_targets(None, project)
    if repo is None:
        raise AssertionError("write scope validation should require repo when no batch flag is set")
    return _selected_repo_targets(repo, project)


def _project_repo_targets(project_id: str) -> list[RepoTargetPayload]:
    return _project_repo_targets_impl(project_id, project_detail=run_project_detail)


def _matching_repo_targets(selector: str, project_ids: list[str]) -> list[RepoTargetPayload]:
    return _matching_repo_targets_impl(selector, project_ids, project_detail=run_project_detail)


def _resolve_single_repo_target(repo: str, project: str | None) -> RepoTargetPayload:
    return _resolve_single_repo_target_impl(
        repo,
        project,
        list_projects=list_projects,
        project_detail=run_project_detail,
        raise_bad_selector=_raise_bad_selector,
    )


def _project_runtime_status(project_id: str) -> ProjectRuntimeStatusPayload:
    project = run_project_detail(project_id)
    project_payload = _json_dict(project.get("project"))
    project_name = _json_str(project_payload.get("name"))
    return {
        "project_id": project_id,
        "project_name": project_name,
        "repos": [
            _repo_runtime_status(project_id, project_name, repo)
            for repo in _json_object_list(project.get("repos"))
            if _json_str(repo.get("id")) is not None
        ],
    }


def _project_inventory_status(project_id: str) -> ProjectRuntimeStatusPayload:
    project = run_project_detail(project_id)
    project_payload = _json_dict(project.get("project"))
    project_name = _json_str(project_payload.get("name"))
    return {
        "project_id": project_id,
        "project_name": project_name,
        "repos": [
            _repo_inventory_status(project_id, project_name, repo)
            for repo in _json_object_list(project.get("repos"))
            if _json_str(repo.get("id")) is not None
        ],
    }


def _project_statuses_for_repo(repo: str, project: str | None) -> list[ProjectRuntimeStatusPayload]:
    grouped: dict[str, ProjectRuntimeStatusPayload] = {}
    for target in _matching_repo_targets(repo, _selected_project_ids(project)):
        project_id = target["project_id"]
        if project_id not in grouped:
            grouped[project_id] = {
                "project_id": project_id,
                "project_name": target["project_name"],
                "repos": [],
            }
        grouped[project_id]["repos"].append(_repo_runtime_status_from_target(target))
    return list(grouped.values())


def _repo_runtime_status(
    project_id: str,
    project_name: str | None,
    repo: dict[str, JsonValue],
) -> RepoRuntimeStatusPayload:
    return _repo_runtime_status_from_target(_repo_target(project_id, project_name, repo))


def _repo_runtime_status_from_target(target: RepoTargetPayload) -> RepoRuntimeStatusPayload:
    repo_id = target["repo_id"]
    active_runs = _current_active_runs(_list_repo_active_runs(repo_id))
    rerun_state = _get_repo_rerun_state(repo_id)
    current_head_sha = _current_head_sha(rerun_state)
    reports = _report_status_from_task_links(repo_id, _list_repo_task_links(repo_id), active_runs, rerun_state)
    return {
        "project_id": target["project_id"],
        "project_name": target["project_name"],
        "repo_id": repo_id,
        "github_owner": target["github_owner"],
        "github_name": target["github_name"],
        "github_repo": target["github_repo"],
        "connected": target["connected"],
        "recon_done": target["recon_done"],
        "scores": target["scores"],
        "score_grades": target["score_grades"],
        "score_summary": target["score_summary"],
        "active_run_count": len(active_runs),
        "active_runs": active_runs,
        "current_head_sha": current_head_sha,
        "last_report_at": reports["last_report_at"],
        "reports": reports,
    }


def _repo_inventory_status(
    project_id: str,
    project_name: str | None,
    repo: dict[str, JsonValue],
) -> RepoRuntimeStatusPayload:
    target = _repo_target(project_id, project_name, repo)
    return {
        "project_id": target["project_id"],
        "project_name": target["project_name"],
        "repo_id": target["repo_id"],
        "github_owner": target["github_owner"],
        "github_name": target["github_name"],
        "github_repo": target["github_repo"],
        "connected": target["connected"],
        "recon_done": target["recon_done"],
        "scores": target["scores"],
        "score_grades": target["score_grades"],
        "score_summary": target["score_summary"],
        "active_run_count": 0,
        "active_runs": [],
        "current_head_sha": None,
        "last_report_at": None,
        "reports": _empty_report_status(target["repo_id"]),
    }


def _repo_status_all_payload(projects: list[ProjectRuntimeStatusPayload]) -> RepoStatusAllPayload:
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "summary": _repo_status_summary(projects),
        "projects": projects,
    }


def _repo_status_summary(projects: list[ProjectRuntimeStatusPayload]) -> RepoStatusSummaryPayload:
    repos = [repo for project in projects for repo in project["repos"]]
    return {
        "project_count": len(projects),
        "repo_count": len(repos),
        "connected_repo_count": sum(1 for repo in repos if repo["connected"] is True),
        "active_run_count": sum(repo["active_run_count"] for repo in repos),
        "recon_done_count": sum(1 for repo in repos if repo["recon_done"] is True),
        "report_complete_count": sum(1 for repo in repos if repo["reports"]["complete"]),
    }


def _selected_report_audits(audits: list[AuditAlias], *, all_reports: bool) -> list[AuditAlias]:
    if all_reports:
        if audits:
            raise ValueError("pass report audits or --all, not both")
        return [audit.alias for audit in REPORT_AUDITS]
    if not audits:
        raise ValueError("pass at least one report audit or --all")
    for audit in audits:
        registry_require_report_audit(audit)
    return audits


def _set_schedule_setting(
    target: RepoTargetPayload,
    audit: ReportAuditDefinition,
    jobs: JsonObjectPayload,
    update: ScheduleSettingsUpdate,
) -> dict[str, JsonValue]:
    existing = _schedule_job_by_kind(jobs, audit.job_kind)
    desired = _schedule_settings_payload_for_job(existing, update)
    if desired is None:
        return _schedule_setting_row(target, audit, existing, changed=False, status="unchanged")
    if existing is not None and _schedule_effective_state(existing) == _schedule_effective_state(desired):
        return _schedule_setting_row(target, audit, existing, changed=False, status="unchanged")
    response = _set_schedule(target["repo_id"], audit.alias, desired)
    job = _json_dict(response.get("job")) or desired
    return _schedule_setting_row(target, audit, job, changed=True, status="changed")


def _raise_bad_selector(message: str) -> Never:
    raise EnjiApiError("BAD_SELECTOR", message)


def _audit_run_task_body(project_id: str, repo_id: str, action_key: str) -> JsonObjectPayload:
    return _audit_run_task_body_from_context(
        project_id, repo_id, action_key, run_project_detail(project_id), run_catalog()
    )


def _audit_run_task_body_from_context(
    project_id: str,
    repo_id: str,
    action_key: str,
    project: JsonObjectPayload,
    catalog: JsonObjectPayload,
) -> JsonObjectPayload:
    repo = _project_repo(project, repo_id)
    action = _catalog_action(catalog, action_key)
    runbook_id = _required_str(action, "fleetRunbookId", f"curated action {action_key} has no Fleet runbook")
    runbook = run_runbook(runbook_id)
    repo_full_name = _repo_full_name(repo)
    return {
        "title": f"{_action_title(action)} for {repo_full_name}",
        "description": _task_description(action, repo, _linked_web_resources(project, repo_id)),
        "project_id": project_id,
        "execution_flow": _json_str(runbook.get("suggested_flow")) or DEFAULT_EXECUTION_FLOW,
        "flow_config": _json_object_or_default(runbook.get("suggested_flow_config")),
        "runbook_id": runbook_id,
        "scope_type": "project",
        "scope_owner": project_id,
        "origin_type": "manual",
        "repo_access_contexts": [{"provider": "github", "repo_full_name": repo_full_name}],
    }
