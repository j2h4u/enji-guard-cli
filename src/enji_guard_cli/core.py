import time
from collections.abc import Callable
from typing import Never

from enji_guard_cli.audits import AuditAlias
from enji_guard_cli.audits import require_report_audit as registry_require_report_audit
from enji_guard_cli.auth import AuthError as AuthError
from enji_guard_cli.auth import AuthRefreshPayload as AuthRefreshPayload
from enji_guard_cli.auth import AuthStatusPayload as AuthStatusPayload
from enji_guard_cli.auth import ImportCredentialPayload as ImportCredentialPayload
from enji_guard_cli.auth import import_bearer_token as import_bearer_token
from enji_guard_cli.auth import import_cookie as import_cookie
from enji_guard_cli.auth import refresh_auth as refresh_auth
from enji_guard_cli.core_impl import audit_runs as _audit_runs
from enji_guard_cli.core_impl import report_reads as _report_reads
from enji_guard_cli.core_impl import report_wait as _report_wait
from enji_guard_cli.core_impl.models import (
    DEFAULT_REPO_SORT,
    AuditRunBatchPayload,
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
from enji_guard_cli.core_impl.payloads import json_object_payload as _json_object_payload
from enji_guard_cli.core_impl.preflight import report_start_preflight_payload as _report_start_preflight_payload
from enji_guard_cli.core_impl.project_admin import MoveRepoDependencies as _MoveRepoDependencies
from enji_guard_cli.core_impl.project_admin import connect_repo_payload as _connect_repo_payload
from enji_guard_cli.core_impl.project_admin import create_project_payload as _create_project_payload
from enji_guard_cli.core_impl.project_admin import delete_project_payload as _delete_project_payload
from enji_guard_cli.core_impl.project_admin import move_repo_payload as _move_repo_payload
from enji_guard_cli.core_impl.project_admin import rename_project_payload as _rename_project_payload
from enji_guard_cli.core_impl.repo_status import current_active_runs as _current_active_runs
from enji_guard_cli.core_impl.repo_status import current_head_sha as _current_head_sha
from enji_guard_cli.core_impl.repo_status import empty_report_status as _empty_report_status
from enji_guard_cli.core_impl.repo_status import report_status_from_task_links as _report_status_from_task_links
from enji_guard_cli.core_impl.repo_status import sort_project_repos as _sort_project_repos
from enji_guard_cli.core_impl.selectors import parse_github_repo as _parse_github_repo
from enji_guard_cli.core_impl.selectors import repo_candidate as _repo_candidate
from enji_guard_cli.core_impl.selectors import repo_target as _repo_target
from enji_guard_cli.core_impl.selectors import targeted_run_payload as _targeted_run_payload
from enji_guard_cli.core_impl.selectors import transfer_schedule_replacements as _transfer_schedule_replacements
from enji_guard_cli.core_impl.selectors import validate_write_scope as _validate_write_scope
from enji_guard_cli.core_impl.selectors import validated_project_name as _validated_project_name
from enji_guard_cli.core_impl.status_views import RuntimeStatusDependencies as _RuntimeStatusDependencies
from enji_guard_cli.core_impl.status_views import project_inventory_status as _project_inventory_status_impl
from enji_guard_cli.core_impl.status_views import project_runtime_status as _project_runtime_status_impl
from enji_guard_cli.core_impl.status_views import project_statuses_for_repo as _project_statuses_for_repo_impl
from enji_guard_cli.core_impl.status_views import repo_inventory_status as _repo_inventory_status_impl
from enji_guard_cli.core_impl.status_views import repo_runtime_status as _repo_runtime_status_impl
from enji_guard_cli.core_impl.status_views import (
    repo_runtime_status_from_target as _repo_runtime_status_from_target_impl,
)
from enji_guard_cli.core_impl.status_views import repo_status_all_payload as _repo_status_all_payload_impl
from enji_guard_cli.core_impl.targets import matching_repo_targets as _matching_repo_targets_impl
from enji_guard_cli.core_impl.targets import project_refs as _project_refs_impl
from enji_guard_cli.core_impl.targets import project_repo_targets as _project_repo_targets_impl
from enji_guard_cli.core_impl.targets import resolve_single_project_id as _resolve_single_project_id_impl
from enji_guard_cli.core_impl.targets import resolve_single_repo_target as _resolve_single_repo_target_impl
from enji_guard_cli.core_impl.targets import selected_project_ids as _selected_project_ids_impl
from enji_guard_cli.core_impl.targets import selected_repo_targets as _selected_repo_targets_impl
from enji_guard_cli.core_impl.write_settings import EmailReadDependencies as _EmailReadDependencies
from enji_guard_cli.core_impl.write_settings import EmailWriteDependencies as _EmailWriteDependencies
from enji_guard_cli.core_impl.write_settings import ScheduleReadDependencies as _ScheduleReadDependencies
from enji_guard_cli.core_impl.write_settings import ScheduleWriteDependencies as _ScheduleWriteDependencies
from enji_guard_cli.core_impl.write_settings import WriteScopeDependencies as _WriteScopeDependencies
from enji_guard_cli.core_impl.write_settings import list_email_preferences as _list_email_preferences
from enji_guard_cli.core_impl.write_settings import list_schedule_settings as _list_schedule_settings
from enji_guard_cli.core_impl.write_settings import selected_write_repo_targets as _selected_write_repo_targets_impl
from enji_guard_cli.core_impl.write_settings import set_email_preferences as _set_email_preferences
from enji_guard_cli.core_impl.write_settings import set_schedule_settings as _set_schedule_settings
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
        project_detail=run_project_detail,
        delete_project=run_delete_project,
    )


def list_project_inventory(project: str | None, sort: RepoSort = DEFAULT_REPO_SORT) -> RepoStatusAllPayload:
    project_ids = _selected_project_ids(project)
    project_status = _project_runtime_status if sort == "latest-report" else _project_inventory_status
    projects = [project_status(project_id) for project_id in project_ids]
    _sort_project_repos(projects, sort)
    return _repo_status_all_payload(projects)


def connect_repo(github_repo: str, project: str | None) -> JsonObjectPayload:
    existing = _matching_repo_targets(github_repo, _selected_project_ids(None))
    if existing:
        candidates = ", ".join(_repo_candidate(match) for match in existing)
        raise ValueError(f"repo is already present in Enji Guard: {github_repo}. candidates: {candidates}")
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
    return _report_wait.wait_for_report_completion(
        repo_id,
        options=options,
        heartbeat=heartbeat,
        dependencies=_report_wait.ReportWaitDependencies(
            read_status=_report_status,
            monotonic=time.monotonic,
            sleep=time.sleep,
        ),
    )


def start_audit(
    repo_id: str,
    project_id: str,
    audit: AuditAlias,
) -> JsonObjectPayload | AuditRunSkippedPayload:
    return _audit_runs.start_audit(
        repo_id,
        project_id,
        audit,
        dependencies=_start_audit_dependencies(),
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
    preflight = _report_start_preflight_payload(_report_status(target["repo_id"]))
    return _targeted_run_payload(
        target,
        {
            "preflight": preflight,
            **_start_report_audits_for_target(target["repo_id"], target["project_id"], selected_audits),
        },
    )


def _start_report_audits_for_target(
    repo_id: str,
    project_id: str,
    audits: list[AuditAlias],
) -> AuditRunBatchPayload:
    return _audit_runs.start_report_audits_for_target(
        repo_id,
        project_id,
        audits,
        dependencies=_start_audit_dependencies(),
        get_repo_rerun_state=_get_repo_rerun_state,
    )


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
    return _list_email_preferences(
        repo,
        project,
        dependencies=_EmailReadDependencies(
            selected_repo_targets=_selected_repo_targets,
            get_audit_email_preferences=_get_audit_email_preferences,
        ),
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
    return _set_email_preferences(
        repo,
        project,
        update,
        selected_write_repo_targets=lambda selected_repo, selected_project: _selected_write_repo_targets(
            selected_repo,
            selected_project,
            all_repos=all_repos,
            all_projects=all_projects,
            operation="email set",
        ),
        dependencies=_EmailWriteDependencies(
            put_audit_email_preferences=run_put_audit_email_preferences,
        ),
    )


def _list_schedules(repo_id: str) -> JsonObjectPayload:
    return run_improvement_jobs(repo_id)


def list_schedule_settings(repo: str | None, project: str | None) -> JsonObjectPayload:
    return _list_schedule_settings(
        repo,
        project,
        dependencies=_ScheduleReadDependencies(
            selected_repo_targets=_selected_repo_targets,
            list_schedules=_list_schedules,
        ),
    )


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
    return _set_schedule_settings(
        repo,
        project,
        update,
        selected_write_repo_targets=lambda selected_repo, selected_project: _selected_write_repo_targets(
            selected_repo,
            selected_project,
            all_repos=all_repos,
            all_projects=all_projects,
            operation="schedule set",
        ),
        dependencies=_ScheduleWriteDependencies(
            list_schedules=_list_schedules,
            set_schedule=_set_schedule,
        ),
    )


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
    return _selected_write_repo_targets_impl(
        repo,
        project,
        all_repos=all_repos,
        all_projects=all_projects,
        dependencies=_WriteScopeDependencies(
            validate_write_scope=lambda selected_repo, selected_project: _validate_write_scope(
                selected_repo,
                selected_project,
                all_repos=all_repos,
                all_projects=all_projects,
                operation=operation,
            ),
            selected_repo_targets=_selected_repo_targets,
        ),
    )


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
    return _project_runtime_status_impl(
        project_id,
        project_detail=run_project_detail,
        repo_runtime_status=_repo_runtime_status,
    )


def _project_inventory_status(project_id: str) -> ProjectRuntimeStatusPayload:
    return _project_inventory_status_impl(
        project_id,
        project_detail=run_project_detail,
        repo_inventory_status=_repo_inventory_status,
    )


def _project_statuses_for_repo(repo: str, project: str | None) -> list[ProjectRuntimeStatusPayload]:
    return _project_statuses_for_repo_impl(
        repo,
        project,
        matching_repo_targets=_matching_repo_targets,
        selected_project_ids=_selected_project_ids,
        repo_runtime_status_from_target=_repo_runtime_status_from_target,
    )


def _repo_runtime_status(
    project_id: str,
    project_name: str | None,
    repo: dict[str, JsonValue],
) -> RepoRuntimeStatusPayload:
    return _repo_runtime_status_impl(
        project_id,
        project_name,
        repo,
        repo_target=_repo_target,
        repo_runtime_status_from_target=_repo_runtime_status_from_target,
    )


def _repo_runtime_status_from_target(target: RepoTargetPayload) -> RepoRuntimeStatusPayload:
    return _repo_runtime_status_from_target_impl(
        target,
        dependencies=_RuntimeStatusDependencies(
            list_repo_active_runs=_list_repo_active_runs,
            get_repo_rerun_state=_get_repo_rerun_state,
            list_repo_task_links=_list_repo_task_links,
            current_active_runs=_current_active_runs,
            current_head_sha=_current_head_sha,
            report_status_from_task_links=_report_status_from_task_links,
        ),
    )


def _repo_inventory_status(
    project_id: str,
    project_name: str | None,
    repo: dict[str, JsonValue],
) -> RepoRuntimeStatusPayload:
    return _repo_inventory_status_impl(
        project_id,
        project_name,
        repo,
        repo_target=_repo_target,
        empty_report_status=_empty_report_status,
    )


def _repo_status_all_payload(projects: list[ProjectRuntimeStatusPayload]) -> RepoStatusAllPayload:
    return _repo_status_all_payload_impl(projects)


def _selected_report_audits(audits: list[AuditAlias], *, all_reports: bool) -> list[AuditAlias]:
    return _audit_runs.selected_report_audits(audits, all_reports=all_reports)


def _raise_bad_selector(message: str) -> Never:
    raise EnjiApiError("BAD_SELECTOR", message)


def _make_audit_run_create(
    repo_id: str,
    project_id: str,
    action_key: str,
    fleet_task_body: JsonObjectPayload,
) -> AuditRunCreate:
    return AuditRunCreate(
        repo_id=repo_id,
        project_id=project_id,
        action_key=action_key,
        fleet_task_body=fleet_task_body,
    )


def _start_audit_dependencies() -> _audit_runs.StartAuditDependencies[AuditRunCreate]:
    return _audit_runs.StartAuditDependencies(
        list_repo_active_runs=_list_repo_active_runs,
        make_audit_run_create=_make_audit_run_create,
        start_audit_run=run_start_audit_run,
        project_detail=run_project_detail,
        catalog=run_catalog,
        runbook=run_runbook,
    )
