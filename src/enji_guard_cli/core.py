import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Never, cast

from enji_guard_cli.audits import REPORT_AUDITS, AuditAlias, AuditDefinition
from enji_guard_cli.audits import require_report_audit as registry_require_report_audit
from enji_guard_cli.audits import resolve_audit as registry_resolve_audit
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
    AuditWaitPayload,
    EmailPreferenceUpdate,
    ProjectRef,
    ProjectRuntimeStatusPayload,
    RepoResolvePayload,
    ReportReadItemPayload,
    ReportReadPayload,
    ReportStatusPayload,
    ReportWaitCallback,
    ReportWaitOptions,
    ReportWaitPayload,
    RepoRuntimeStatusPayload,
    RepoSort,
    RepoStatusAllPayload,
    RepoStatusPayload,
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
from enji_guard_cli.core_impl.operations import OPERATION_SPECS as OPERATION_SPECS
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
from enji_guard_cli.core_impl.repo_status import active_runs_for_action as _active_runs_for_action
from enji_guard_cli.core_impl.repo_status import audit_wait_payload as _audit_wait_payload
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
from enji_guard_cli.core_impl.repo_status import validate_wait_options as _validate_wait_options
from enji_guard_cli.core_impl.repo_status import watched_active_runs as _watched_active_runs
from enji_guard_cli.core_impl.schedules import schedule_effective_state as _schedule_effective_state
from enji_guard_cli.core_impl.schedules import schedule_job_by_kind as _schedule_job_by_kind
from enji_guard_cli.core_impl.schedules import schedule_setting_row as _schedule_setting_row
from enji_guard_cli.core_impl.schedules import schedule_settings_payload as _schedule_settings_payload
from enji_guard_cli.core_impl.schedules import schedule_settings_payload_for_job as _schedule_settings_payload_for_job
from enji_guard_cli.core_impl.schedules import validate_schedule_settings_update as _validate_schedule_settings_update
from enji_guard_cli.core_impl.selectors import ambiguous_project_message as _ambiguous_project_message
from enji_guard_cli.core_impl.selectors import ambiguous_repo_message as _ambiguous_repo_message
from enji_guard_cli.core_impl.selectors import parse_github_repo as _parse_github_repo
from enji_guard_cli.core_impl.selectors import project_candidates as _project_candidates
from enji_guard_cli.core_impl.selectors import project_ref_matches as _project_ref_matches
from enji_guard_cli.core_impl.selectors import repo_target as _repo_target
from enji_guard_cli.core_impl.selectors import repo_target_matches as _repo_target_matches
from enji_guard_cli.core_impl.selectors import targeted_run_payload as _targeted_run_payload
from enji_guard_cli.core_impl.selectors import transfer_schedule_replacements as _transfer_schedule_replacements
from enji_guard_cli.core_impl.selectors import validate_write_scope as _validate_write_scope
from enji_guard_cli.core_impl.selectors import validated_project_name as _validated_project_name
from enji_guard_cli.enji_api import REPORTS_LIST_DEFAULT_SELECTOR as REPORTS_LIST_DEFAULT_SELECTOR
from enji_guard_cli.enji_api import (
    AuditRunCreate,
    JsonObjectPayload,
    JsonValue,
    RepoTransfer,
)
from enji_guard_cli.enji_api import audit_email_preferences as run_audit_email_preferences
from enji_guard_cli.enji_api import audit_summary_snapshot as run_audit_summary_snapshot
from enji_guard_cli.enji_api import catalog as run_catalog
from enji_guard_cli.enji_api import connect_project_repo as run_connect_project_repo
from enji_guard_cli.enji_api import create_project as run_create_project
from enji_guard_cli.enji_api import delete_project as run_delete_project
from enji_guard_cli.enji_api import github_installation_repos as run_github_installation_repos
from enji_guard_cli.enji_api import github_installations as run_github_installations
from enji_guard_cli.enji_api import improvement_jobs as run_improvement_jobs
from enji_guard_cli.enji_api import move_repo as run_move_repo
from enji_guard_cli.enji_api import preflight_repo_move as run_preflight_repo_move
from enji_guard_cli.enji_api import project_active_runs as run_project_active_runs
from enji_guard_cli.enji_api import project_detail as run_project_detail
from enji_guard_cli.enji_api import projects as run_projects
from enji_guard_cli.enji_api import put_audit_email_preferences as run_put_audit_email_preferences
from enji_guard_cli.enji_api import put_improvement_job as run_put_improvement_job
from enji_guard_cli.enji_api import rename_project as run_rename_project
from enji_guard_cli.enji_api import repo_active_runs as run_repo_active_runs
from enji_guard_cli.enji_api import repo_audit_history as run_repo_audit_history
from enji_guard_cli.enji_api import repo_audit_rerun_state as run_repo_audit_rerun_state
from enji_guard_cli.enji_api import repo_task_links as run_repo_task_links
from enji_guard_cli.enji_api import runbook as run_runbook
from enji_guard_cli.enji_api import start_audit_run as run_start_audit_run
from enji_guard_cli.enji_api import update_repo_connection as run_update_repo_connection
from enji_guard_cli.errors import EnjiApiError


def list_projects() -> JsonObjectPayload:
    return run_projects()


def create_project(name: str) -> JsonObjectPayload:
    project_name = _validated_project_name(name)
    return {
        "project_name": project_name,
        "response": run_create_project(project_name),
    }


def rename_project(project: str, name: str) -> JsonObjectPayload:
    project_id = _resolve_single_project_id(project)
    project_name = _validated_project_name(name)
    return {
        "project_id": project_id,
        "project_name": project_name,
        "response": run_rename_project(project_id, project_name),
    }


def delete_project(project: str) -> JsonObjectPayload:
    project_id = _resolve_single_project_id(project)
    run_delete_project(project_id)
    return {"project_id": project_id, "deleted": True}


def list_project_inventory(project: str | None, sort: RepoSort = DEFAULT_REPO_SORT) -> RepoStatusAllPayload:
    project_ids = _selected_project_ids(project)
    project_status = _project_runtime_status if sort == "latest-report" else _project_inventory_status
    projects = [project_status(project_id) for project_id in project_ids]
    _sort_project_repos(projects, sort)
    return _repo_status_all_payload(projects)


def list_github_installations() -> JsonObjectPayload:
    return run_github_installations()


def list_github_repos(installation_id: str) -> JsonObjectPayload:
    return run_github_installation_repos(installation_id)


def add_repo(project_id: str, github_owner: str, github_name: str) -> JsonObjectPayload:
    return run_connect_project_repo(project_id, github_owner, github_name)


def connect_repo(github_repo: str, project: str | None) -> JsonObjectPayload:
    project_id = _resolve_single_project_id(project)
    github_owner, github_name = _parse_github_repo(github_repo)
    return run_connect_project_repo(project_id, github_owner, github_name)


def move_repo(repo: str, source_project: str | None, target_project: str) -> JsonObjectPayload:
    source = _resolve_single_repo_target(repo, source_project)
    target_project_id = _resolve_single_project_id(target_project)
    if source["project_id"] == target_project_id:
        raise ValueError("repo is already in target project")
    preflight = run_preflight_repo_move(source["project_id"], source["repo_id"], target_project_id)
    response = run_move_repo(
        RepoTransfer(
            source_project_id=source["project_id"],
            repo_id=source["repo_id"],
            target_project_id=target_project_id,
            schedule_replacements=_transfer_schedule_replacements(preflight),
        )
    )
    return {
        "repo": cast(JsonValue, dict(source)),
        "source_project_id": source["project_id"],
        "target_project_id": target_project_id,
        "preflight": preflight,
        "response": response,
    }


def set_repo_connection(project_id: str, repo_id: str, *, connected: bool) -> JsonObjectPayload:
    return run_update_repo_connection(project_id, repo_id, connected=connected)


def list_project_active_runs(project_id: str) -> JsonObjectPayload:
    return run_project_active_runs(project_id)


def list_repo_active_runs(repo_id: str) -> JsonObjectPayload:
    return run_repo_active_runs(repo_id)


def get_repo_rerun_state(repo_id: str) -> JsonObjectPayload:
    return run_repo_audit_rerun_state(repo_id)


def list_repo_task_links(repo_id: str) -> JsonObjectPayload:
    return run_repo_task_links(repo_id)


def list_repo_audit_history(repo_id: str) -> JsonObjectPayload:
    return run_repo_audit_history(repo_id)


def report_status(repo_id: str) -> ReportStatusPayload:
    active_runs = _current_active_runs(list_repo_active_runs(repo_id))
    rerun_state = get_repo_rerun_state(repo_id)
    return _report_status_from_task_links(repo_id, list_repo_task_links(repo_id), active_runs, rerun_state)


def repo_status(repo_id: str) -> RepoStatusPayload:
    active_runs = _current_active_runs(list_repo_active_runs(repo_id))
    rerun_state = get_repo_rerun_state(repo_id)
    return {
        "repo_id": repo_id,
        "active_run_count": len(active_runs),
        "active_runs": active_runs,
        "rerun_state": rerun_state,
        "current_head_sha": _current_head_sha(rerun_state),
        "reports": _report_status_from_task_links(repo_id, list_repo_task_links(repo_id), active_runs, rerun_state),
    }


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


def wait_for_audit_completion(
    repo_id: str,
    audit: AuditAlias | None,
    poll_seconds: int,
    timeout_seconds: int,
) -> AuditWaitPayload:
    _validate_wait_options(poll_seconds, timeout_seconds)
    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    action_key = _action_key_for_optional_audit(audit)
    while True:
        active_runs = _watched_active_runs(list_repo_active_runs(repo_id), action_key)
        if not active_runs:
            return _audit_wait_payload(repo_id, audit, True, started_at, active_runs)
        if time.monotonic() >= deadline:
            return _audit_wait_payload(repo_id, audit, False, started_at, active_runs)
        time.sleep(_next_poll_sleep(deadline, poll_seconds))


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
        status = report_status(repo_id)
        payload = _report_wait_payload(repo_id, status, started_at, options=options, timed_out=False)
        if payload["complete"] or payload["reason"] == "failed":
            return payload
        now = time.monotonic()
        if now >= deadline:
            return _report_wait_payload(repo_id, status, started_at, options=options, timed_out=True)
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
    active_runs = _active_runs_for_action(_current_active_runs(list_repo_active_runs(repo_id)), action_key)
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


def start_all_report_audits(repo_id: str, project_id: str) -> AuditRunBatchPayload:
    return _start_report_audits_for_target(repo_id, project_id, [audit.alias for audit in REPORT_AUDITS])


def _start_report_audits_for_target(
    repo_id: str,
    project_id: str,
    audits: list[AuditAlias],
) -> AuditRunBatchPayload:
    runs: list[AuditRunBatchItem] = []
    skipped: list[AuditRunSkippedItem] = []
    active_runs = _current_active_runs(list_repo_active_runs(repo_id))
    rerun_state = get_repo_rerun_state(repo_id)
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


def show_report(repo_id: str, audit: AuditAlias) -> JsonObjectPayload:
    resolved = registry_resolve_audit(audit)
    route_slug = resolved.route_slug
    if route_slug is None:
        raise ValueError("recon does not have an upfront.audit.summary report snapshot")
    return run_audit_summary_snapshot(repo_id, route_slug)


def read_reports_for_repo(
    repo: str,
    project: str | None,
    audits: list[AuditAlias],
    *,
    all_reports: bool,
) -> dict[str, object]:
    target = _resolve_single_repo_target(repo, project)
    selected_audits = _selected_reports_to_read(target["repo_id"], audits, all_reports=all_reports)
    return _targeted_run_payload(target, _read_reports_for_target(target["repo_id"], selected_audits))


def list_email_preferences(repo: str | None, project: str | None) -> JsonObjectPayload:
    return _email_preferences_payload(
        [
            _email_preference_row(target, audit, get_audit_email_preferences(target["repo_id"], audit.action_key))
            for target in _selected_repo_targets(repo, project)
            for audit in REPORT_AUDITS
        ]
    )


def get_audit_email_preferences(repo_id: str, action_key: str) -> JsonObjectPayload:
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


def list_schedules(repo_id: str) -> JsonObjectPayload:
    return run_improvement_jobs(repo_id)


def list_schedule_settings(repo: str | None, project: str | None) -> JsonObjectPayload:
    rows = [
        _schedule_setting_row(target, audit, _schedule_job_by_kind(jobs, audit.job_kind))
        for target in _selected_repo_targets(repo, project)
        for jobs in (list_schedules(target["repo_id"]),)
        for audit in REPORT_AUDITS
    ]
    return _schedule_settings_payload(rows)


def set_schedule(
    repo_id: str,
    audit: AuditAlias,
    payload: object,
) -> JsonObjectPayload:
    resolved = registry_resolve_audit(audit)
    job_kind = resolved.job_kind
    if job_kind is None:
        raise ValueError("recon does not have a schedulable improvement job")
    return run_put_improvement_job(repo_id, job_kind, _json_object_payload(payload))


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
        for jobs in (list_schedules(target["repo_id"]),)
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


def wait_for_work(
    repo: str,
    audit: AuditAlias,
    project: str | None,
    *,
    poll_seconds: int,
    timeout_seconds: int,
) -> dict[str, object]:
    target = _resolve_single_repo_target(repo, project)
    payload = wait_for_audit_completion(target["repo_id"], audit, poll_seconds, timeout_seconds)
    return _targeted_run_payload(target, payload)


def _action_key_for_optional_audit(audit: AuditAlias | None) -> str | None:
    if audit is None:
        return None
    return registry_resolve_audit(audit).action_key


def _selected_project_ids(project: str | None) -> list[str]:
    if project is not None:
        return [_resolve_single_project_id(project)]
    return [
        selected_id
        for project in _json_object_list(list_projects().get("projects"))
        if (selected_id := _json_str(project.get("id"))) is not None
    ]


def _resolve_single_project_id(project: str | None) -> str:
    project_refs = _project_refs()
    if project is None:
        if len(project_refs) == 1:
            return project_refs[0]["id"]
        _raise_bad_selector(_ambiguous_project_message(project_refs))

    matches = [project_ref for project_ref in project_refs if _project_ref_matches(project_ref, project)]
    if not matches:
        _raise_bad_selector(
            f"project selector matched no projects: {project}. candidates: {_project_candidates(project_refs)}"
        )
    if len(matches) > 1:
        _raise_bad_selector(_ambiguous_project_message(matches))
    return matches[0]["id"]


def _project_refs() -> list[ProjectRef]:
    refs: list[ProjectRef] = []
    for project in _json_object_list(list_projects().get("projects")):
        project_id = _json_str(project.get("id"))
        if project_id is None:
            continue
        refs.append({"id": project_id, "name": _json_str(project.get("name"))})
    return refs


def _selected_repo_targets(repo: str | None, project: str | None) -> list[RepoTargetPayload]:
    if repo is not None:
        return [_resolve_single_repo_target(repo, project)]
    return [target for project_id in _selected_project_ids(project) for target in _project_repo_targets(project_id)]


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
    project = run_project_detail(project_id)
    project_payload = _json_dict(project.get("project"))
    project_name = _json_str(project_payload.get("name"))
    return [
        _repo_target(project_id, project_name, repo)
        for repo in _json_object_list(project.get("repos"))
        if _json_str(repo.get("id")) is not None
    ]


def _matching_repo_targets(selector: str, project_ids: list[str]) -> list[RepoTargetPayload]:
    matches: list[RepoTargetPayload] = []
    for project_id in project_ids:
        matches.extend(target for target in _project_repo_targets(project_id) if _repo_target_matches(target, selector))
    return matches


def _resolve_single_repo_target(repo: str, project: str | None) -> RepoTargetPayload:
    matches = _matching_repo_targets(repo, _selected_project_ids(project))
    if not matches:
        _raise_bad_selector(f"repo selector matched no repos: {repo}")
    if len(matches) > 1:
        _raise_bad_selector(_ambiguous_repo_message(repo, matches))
    return matches[0]


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
    active_runs = _current_active_runs(list_repo_active_runs(repo_id))
    rerun_state = get_repo_rerun_state(repo_id)
    current_head_sha = _current_head_sha(rerun_state)
    reports = _report_status_from_task_links(repo_id, list_repo_task_links(repo_id), active_runs, rerun_state)
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


def _selected_reports_to_read(repo_id: str, audits: list[AuditAlias], *, all_reports: bool) -> list[AuditAlias]:
    if audits or all_reports:
        return _selected_report_audits(audits, all_reports=all_reports)
    return [AuditAlias(audit) for audit in report_status(repo_id)["ready"]]


def _read_reports_for_target(repo_id: str, audits: list[AuditAlias]) -> ReportReadPayload:
    rerun_state = get_repo_rerun_state(repo_id)
    current_head_sha = _current_head_sha(rerun_state)
    return {"reports": [_report_read_item(repo_id, audit, current_head_sha, rerun_state) for audit in audits]}


def _report_read_item(
    repo_id: str,
    audit: AuditAlias,
    current_head_sha: str | None,
    rerun_state: JsonObjectPayload,
) -> ReportReadItemPayload:
    action_key = registry_resolve_audit(audit).action_key
    last_audited_head_sha = _last_audited_head_sha(rerun_state, action_key)
    return {
        "audit": audit.value,
        "current_head_sha": current_head_sha,
        "last_audited_head_sha": last_audited_head_sha,
        "out_of_date": _out_of_date(current_head_sha, last_audited_head_sha),
        "snapshot": _json_dict(show_report(repo_id, audit).get("snapshot")),
    }


def _set_schedule_setting(
    target: RepoTargetPayload,
    audit: AuditDefinition,
    jobs: JsonObjectPayload,
    update: ScheduleSettingsUpdate,
) -> dict[str, JsonValue]:
    existing = _schedule_job_by_kind(jobs, audit.job_kind)
    desired = _schedule_settings_payload_for_job(existing, update)
    if desired is None:
        return _schedule_setting_row(target, audit, existing, changed=False, status="unchanged")
    if existing is not None and _schedule_effective_state(existing) == _schedule_effective_state(desired):
        return _schedule_setting_row(target, audit, existing, changed=False, status="unchanged")
    response = set_schedule(target["repo_id"], audit.alias, desired)
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
