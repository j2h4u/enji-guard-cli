import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Never, cast

from enji_guard_cli.audits import AuditCatalog, AuditDefinition
from enji_guard_cli.auth import AuthError as AuthError
from enji_guard_cli.auth import AuthRefreshPayload as AuthRefreshPayload
from enji_guard_cli.auth import AuthStatusPayload as AuthStatusPayload
from enji_guard_cli.auth import ImportCredentialPayload as ImportCredentialPayload
from enji_guard_cli.auth import import_bearer_token as import_bearer_token
from enji_guard_cli.auth import import_cookie as import_cookie
from enji_guard_cli.auth import refresh_auth as refresh_auth
from enji_guard_cli.core_impl import audit_runs as _audit_runs
from enji_guard_cli.core_impl import report_workflows as _report_workflows
from enji_guard_cli.core_impl.catalog import parse_audit_catalog as _parse_audit_catalog
from enji_guard_cli.core_impl.models import (
    DEFAULT_REPO_SORT,
    AuditRunBatchPayload,
    AuditRunSkippedPayload,
    EmailPreferenceUpdate,
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
from enji_guard_cli.core_impl.project_admin import MoveRepoDependencies as _MoveRepoDependencies
from enji_guard_cli.core_impl.project_admin import ProjectCrudDependencies as _ProjectCrudDependencies
from enji_guard_cli.core_impl.project_admin import activate_existing_repo_payload as _activate_existing_repo_payload
from enji_guard_cli.core_impl.project_admin import add_repo_payload as _add_repo_payload
from enji_guard_cli.core_impl.project_admin import create_project as _create_project_impl
from enji_guard_cli.core_impl.project_admin import delete_project as _delete_project_impl
from enji_guard_cli.core_impl.project_admin import move_repo_payload as _move_repo_payload
from enji_guard_cli.core_impl.project_admin import remove_repo_payload as _remove_repo_payload
from enji_guard_cli.core_impl.project_admin import rename_project as _rename_project_impl
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
from enji_guard_cli.core_impl.status_views import RepoInventoryStatusContext as _RepoInventoryStatusContext
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
from enji_guard_cli.core_impl.targets import resolve_single_project_id as _resolve_single_project_id_impl
from enji_guard_cli.core_impl.targets import resolve_single_repo_target as _resolve_single_repo_target_impl
from enji_guard_cli.core_impl.targets import selected_project_ids as _selected_project_ids_impl
from enji_guard_cli.core_impl.targets import selected_repo_targets as _selected_repo_targets_impl
from enji_guard_cli.core_impl.write_settings import EmailReadDependencies as _EmailReadDependencies
from enji_guard_cli.core_impl.write_settings import EmailWriteDependencies as _EmailWriteDependencies
from enji_guard_cli.core_impl.write_settings import ScheduleReadDependencies as _ScheduleReadDependencies
from enji_guard_cli.core_impl.write_settings import ScheduleWriteDependencies as _ScheduleWriteDependencies
from enji_guard_cli.core_impl.write_settings import SetEmailPreferencesContext as _SetEmailPreferencesContext
from enji_guard_cli.core_impl.write_settings import SetScheduleSettingsContext as _SetScheduleSettingsContext
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
from enji_guard_cli.enji_api import add_project_repo as run_add_project_repo
from enji_guard_cli.enji_api import audit_email_preferences as run_audit_email_preferences
from enji_guard_cli.enji_api import audit_summary_snapshot as run_audit_summary_snapshot
from enji_guard_cli.enji_api import catalog as run_catalog
from enji_guard_cli.enji_api import connect_project_repo as run_connect_project_repo
from enji_guard_cli.enji_api import create_project as run_create_project
from enji_guard_cli.enji_api import delete_project as run_delete_project
from enji_guard_cli.enji_api import delete_project_repo as run_delete_project_repo
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
from enji_guard_cli.enji_api import task_detail as run_task_detail
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue
from enji_guard_cli.settings import default_settings


def list_projects() -> JsonObjectPayload:
    return run_projects()


def _catalog_context() -> tuple[AuditCatalog, JsonObjectPayload]:
    """Fetch and parse the live catalog once for one top-level operation."""

    payload = run_catalog()
    return _parse_audit_catalog(payload), payload


def _project_crud_dependencies() -> _ProjectCrudDependencies:
    return _ProjectCrudDependencies(
        list_projects=run_projects,
        resolve_single_project_id=_resolve_single_project_id,
        validate_project_name=_validated_project_name,
        create_project=run_create_project,
        rename_project=run_rename_project,
        project_detail=run_project_detail,
        delete_project=run_delete_project,
    )


def create_project(name: str) -> JsonObjectPayload:
    return _create_project_impl(name, dependencies=_project_crud_dependencies())


def rename_project(project: str, name: str) -> JsonObjectPayload:
    return _rename_project_impl(project, name, dependencies=_project_crud_dependencies())


def delete_project(project: str) -> JsonObjectPayload:
    return _delete_project_impl(project, dependencies=_project_crud_dependencies())


def list_project_inventory(project: str | None, sort: RepoSort = DEFAULT_REPO_SORT) -> RepoStatusAllPayload:
    catalog, _ = _catalog_context()
    project_ids = _selected_project_ids(project)
    project_status = _project_runtime_status if sort == "latest-report" else _project_inventory_status
    projects = [project_status(project_id, catalog) for project_id in project_ids]
    _sort_project_repos(projects, sort)
    return _repo_status_all_payload(projects)


def add_repo(github_repo: str, project: str | None) -> JsonObjectPayload:
    existing = _matching_repo_targets(github_repo, _selected_project_ids(project))
    if existing:
        if len(existing) == 1:
            payload = _activate_existing_repo_payload(existing[0], connect_project_repo=run_connect_project_repo)
            target = _resolve_single_repo_target(github_repo, existing[0]["project_id"])
            return _add_repo_with_recon(payload, target)
        candidates = ", ".join(_repo_candidate(match) for match in existing)
        raise ValueError(f"repo is already present in multiple Enji projects: {github_repo}. candidates: {candidates}")
    payload = _add_repo_payload(
        github_repo,
        project,
        resolve_single_project_id=_resolve_single_project_id,
        parse_github_repo=_parse_github_repo,
        add_project_repo=run_add_project_repo,
    )
    target = _resolve_single_repo_target(github_repo, project)
    if target.get("connected") is not True:
        run_connect_project_repo(target["project_id"], target["repo_id"])
        target = _resolve_single_repo_target(github_repo, project)
    return _add_repo_with_recon(payload, target)


def _add_repo_with_recon(payload: JsonObjectPayload, target: RepoTargetPayload) -> JsonObjectPayload:
    if target.get("recon_done") is True:
        payload["recon"] = {
            "skipped": True,
            "audit": "audit.recon",
            "action_key": "audit.recon",
            "reason": "already_done",
        }
        return payload
    catalog, catalog_payload = _catalog_context()
    payload["recon"] = cast(
        JsonValue,
        start_audit(target["repo_id"], target["project_id"], catalog.recon, catalog_payload, catalog),
    )
    return payload


def remove_repo(repo: str, project: str | None) -> JsonObjectPayload:
    try:
        target = _resolve_single_repo_target(repo, project)
    except EnjiApiError as exc:
        if exc.code == "BAD_SELECTOR" and "/" in repo:
            return {
                "repo": None,
                "project_id": None,
                "repo_id": None,
                "removed": False,
                "already_absent": True,
                "selector": repo,
            }
        raise
    return _remove_repo_payload(
        repo,
        project,
        resolve_single_repo_target=lambda selected_repo, selected_project: _repo_target_from_resolved(
            selected_repo, selected_project, target
        ),
        delete_project_repo=run_delete_project_repo,
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


def _repo_target_from_resolved(
    repo: str,
    project: str | None,
    resolved_target: RepoTargetPayload,
) -> RepoTargetPayload:
    if repo == resolved_target["repo_id"] or project == resolved_target["project_id"]:
        return resolved_target
    return _resolve_single_repo_target(repo, project)


def _get_repo_rerun_state(repo_id: str, catalog: AuditCatalog) -> JsonObjectPayload:
    return _report_workflows.get_repo_rerun_state(
        repo_id, dependencies=_report_workflow_dependencies(catalog, {"curatedActions": []})
    )


def _list_repo_task_links(repo_id: str, catalog: AuditCatalog) -> JsonObjectPayload:
    return _report_workflows.list_repo_task_links(
        repo_id, dependencies=_report_workflow_dependencies(catalog, {"curatedActions": []})
    )


def _report_status(repo_id: str, catalog: AuditCatalog) -> ReportStatusPayload:
    return _report_workflows.report_status(
        repo_id, dependencies=_report_workflow_dependencies(catalog, {"curatedActions": []})
    )


def repo_status_all(project_id: str | None, sort: RepoSort = DEFAULT_REPO_SORT) -> RepoStatusAllPayload:
    catalog, _ = _catalog_context()
    projects = [
        _project_runtime_status(selected_project_id, catalog)
        for selected_project_id in _selected_project_ids(project_id)
    ]
    _sort_project_repos(projects, sort)
    return _repo_status_all_payload(projects)


def runtime_status(repo: str | None, project: str | None, sort: RepoSort = DEFAULT_REPO_SORT) -> RepoStatusAllPayload:
    catalog, _ = _catalog_context()
    if repo is None:
        projects = [_project_runtime_status(project_id, catalog) for project_id in _selected_project_ids(project)]
        _sort_project_repos(projects, sort)
        return _repo_status_all_payload(projects)

    projects = _project_statuses_for_repo(repo, project, catalog)
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
    catalog: AuditCatalog,
) -> ReportWaitPayload:
    return _report_workflows.wait_for_report_completion(
        repo_id,
        options=options,
        heartbeat=heartbeat,
        dependencies=_report_workflow_dependencies(catalog, {"curatedActions": []}),
    )


def start_audit(
    repo_id: str,
    project_id: str,
    audit: AuditDefinition,
    catalog_payload: JsonObjectPayload | None = None,
    catalog: AuditCatalog | None = None,
) -> JsonObjectPayload | AuditRunSkippedPayload:
    if catalog is None or catalog_payload is None:
        catalog, catalog_payload = _catalog_context()
    return _audit_runs.start_audit(
        repo_id,
        project_id,
        audit,
        catalog_payload,
        dependencies=_start_audit_dependencies(catalog, catalog_payload),
    )


def start_recon(repo: str, project: str | None) -> dict[str, object]:
    target = _resolve_single_repo_target(repo, project)
    catalog, catalog_payload = _catalog_context()
    return _targeted_run_payload(
        target,
        start_audit(target["repo_id"], target["project_id"], catalog.recon, catalog_payload, catalog),
    )


def start_report_audits(
    repo: str,
    project: str | None,
    audits: list[str],
    *,
    all_reports: bool,
) -> dict[str, object]:
    return _report_workflows.start_report_audits(
        repo,
        project,
        audits,
        all_reports=all_reports,
        dependencies=_report_workflow_dependencies(*_catalog_context()),
    )


def _start_report_audits_for_target(
    repo_id: str,
    project_id: str,
    audits: list[AuditDefinition],
    catalog: AuditCatalog,
    catalog_payload: JsonObjectPayload | None = None,
) -> AuditRunBatchPayload:
    return _report_workflows.start_report_audits_for_target(
        repo_id,
        project_id,
        audits,
        dependencies=_report_workflow_dependencies(catalog, catalog_payload or {"curatedActions": []}),
    )


def read_reports_for_repo(
    repo: str,
    project: str | None,
    audits: list[str],
    *,
    all_reports: bool,
) -> dict[str, object]:
    return _report_workflows.read_reports_for_repo(
        repo,
        project,
        audits,
        all_reports=all_reports,
        dependencies=_report_workflow_dependencies(*_catalog_context()),
    )


def _read_report_snapshot(
    repo_id: str, audit: AuditDefinition, catalog: AuditCatalog, catalog_payload: JsonObjectPayload
) -> JsonObjectPayload:
    return _report_workflows.read_report_snapshot(
        repo_id, audit, dependencies=_report_workflow_dependencies(catalog, catalog_payload)
    )


def list_email_preferences(repo: str | None, project: str | None) -> JsonObjectPayload:
    catalog, _ = _catalog_context()
    return _list_email_preferences(
        repo,
        project,
        dependencies=_EmailReadDependencies(
            selected_repo_targets=_selected_repo_targets,
            get_audit_email_preferences=_get_audit_email_preferences,
        ),
        catalog=catalog,
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
    catalog, _ = _catalog_context()
    return _set_email_preferences(
        _SetEmailPreferencesContext(repo, project, update, catalog),
        selected_write_repo_targets=lambda selected_repo, selected_project: _selected_write_repo_targets(
            selected_repo,
            selected_project,
            all_repos=all_repos,
            all_projects=all_projects,
            operation="email set",
        ),
        dependencies=_EmailWriteDependencies(
            get_audit_email_preferences=run_audit_email_preferences,
            put_audit_email_preferences=run_put_audit_email_preferences,
        ),
    )


def _list_schedules(repo_id: str) -> JsonObjectPayload:
    return run_improvement_jobs(repo_id)


def list_schedule_settings(repo: str | None, project: str | None) -> JsonObjectPayload:
    catalog, _ = _catalog_context()
    return _list_schedule_settings(
        repo,
        project,
        dependencies=_ScheduleReadDependencies(
            selected_repo_targets=_selected_repo_targets,
            list_schedules=_list_schedules,
        ),
        catalog=catalog,
    )


def _set_schedule(
    repo_id: str,
    audit: AuditDefinition,
    payload: object,
) -> JsonObjectPayload:
    return run_put_improvement_job(repo_id, audit.runbook_kind, _json_object_payload(payload))


def set_schedule_settings(
    repo: str | None,
    project: str | None,
    update: ScheduleSettingsUpdate,
    *,
    all_repos: bool = False,
    all_projects: bool = False,
) -> JsonObjectPayload:
    catalog, _ = _catalog_context()
    return _set_schedule_settings(
        _SetScheduleSettingsContext(repo, project, update, catalog),
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
    catalog, catalog_payload = _catalog_context()
    return _report_workflows.wait_for_reports(
        repo,
        project,
        options=options,
        heartbeat=heartbeat,
        dependencies=_report_workflow_dependencies(catalog, catalog_payload),
    )


def _selected_project_ids(project: str | None) -> list[str]:
    return _selected_project_ids_impl(project, list_projects=list_projects, raise_bad_selector=_raise_bad_selector)


def _resolve_single_project_id(project: str | None) -> str:
    return _resolve_single_project_id_impl(project, list_projects=list_projects, raise_bad_selector=_raise_bad_selector)


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


def _project_runtime_status(project_id: str, catalog: AuditCatalog) -> ProjectRuntimeStatusPayload:
    return _project_runtime_status_impl(
        project_id,
        project_detail=run_project_detail,
        repo_runtime_status=lambda project_id, project_name, repo: _repo_runtime_status(
            project_id, project_name, repo, catalog
        ),
    )


def _project_inventory_status(project_id: str, catalog: AuditCatalog) -> ProjectRuntimeStatusPayload:
    return _project_inventory_status_impl(
        project_id,
        project_detail=run_project_detail,
        repo_inventory_status=lambda project_id, project_name, repo: _repo_inventory_status(
            project_id, project_name, repo, catalog
        ),
    )


def _project_statuses_for_repo(
    repo: str, project: str | None, catalog: AuditCatalog
) -> list[ProjectRuntimeStatusPayload]:
    return _project_statuses_for_repo_impl(
        repo,
        project,
        matching_repo_targets=_matching_repo_targets,
        selected_project_ids=_selected_project_ids,
        repo_runtime_status_from_target=lambda target: _repo_runtime_status_from_target(target, catalog),
    )


def _repo_runtime_status(
    project_id: str,
    project_name: str | None,
    repo: dict[str, JsonValue],
    catalog: AuditCatalog,
) -> RepoRuntimeStatusPayload:
    return _repo_runtime_status_impl(
        project_id,
        project_name,
        repo,
        repo_target=_repo_target,
        repo_runtime_status_from_target=lambda target: _repo_runtime_status_from_target(target, catalog),
    )


def _repo_runtime_status_from_target(target: RepoTargetPayload, catalog: AuditCatalog) -> RepoRuntimeStatusPayload:
    return _repo_runtime_status_from_target_impl(
        target,
        dependencies=_RuntimeStatusDependencies(
            repo_active_runs=lambda repo_id: _merged_repo_active_runs(repo_id, catalog),
            get_repo_rerun_state=lambda repo_id: _get_repo_rerun_state(repo_id, catalog),
            list_repo_task_links=lambda repo_id: _list_repo_task_links(repo_id, catalog),
            current_head_sha=_current_head_sha,
            report_status_from_task_links=_report_status_from_task_links,
            catalog=catalog,
        ),
    )


def _repo_inventory_status(
    project_id: str,
    project_name: str | None,
    repo: dict[str, JsonValue],
    catalog: AuditCatalog,
) -> RepoRuntimeStatusPayload:
    return _repo_inventory_status_impl(
        _RepoInventoryStatusContext(project_id, project_name, repo, catalog),
        repo_target=_repo_target,
        empty_report_status=_empty_report_status,
    )


def _repo_status_all_payload(projects: list[ProjectRuntimeStatusPayload]) -> RepoStatusAllPayload:
    return _repo_status_all_payload_impl(projects)


def _merged_repo_active_runs(
    repo_id: str,
    catalog: AuditCatalog,
    *,
    rerun_state: JsonObjectPayload | None = None,
    task_links: JsonObjectPayload | None = None,
) -> list[JsonValue]:
    return _report_workflows.merged_repo_active_runs(
        repo_id,
        rerun_state=rerun_state,
        task_links=task_links,
        dependencies=_report_workflow_dependencies(catalog, {"curatedActions": []}),
    )


def _report_workflow_dependencies(
    catalog: AuditCatalog,
    catalog_payload: JsonObjectPayload,
) -> _report_workflows.ReportWorkflowDependencies[AuditRunCreate]:
    return _report_workflows.ReportWorkflowDependencies(
        list_repo_active_runs=run_repo_active_runs,
        get_repo_rerun_state=run_repo_audit_rerun_state,
        list_repo_task_links=run_repo_task_links,
        report_status_from_task_links=_report_status_from_task_links,
        resolve_single_repo_target=_resolve_single_repo_target,
        targeted_run_payload=_targeted_run_payload,
        report_status=lambda repo_id: _report_workflows.report_status(
            repo_id, dependencies=_report_workflow_dependencies(catalog, catalog_payload)
        ),
        report_snapshot=run_audit_summary_snapshot,
        read_report_snapshot=lambda repo_id, audit: _read_report_snapshot(repo_id, audit, catalog, catalog_payload),
        wait_for_report_completion=lambda repo_id, *, options, heartbeat: wait_for_report_completion(
            repo_id,
            options=options,
            heartbeat=heartbeat,
            catalog=catalog,
        ),
        start_report_audits_for_target=lambda repo_id, project_id, audits: _start_report_audits_for_target(
            repo_id, project_id, audits, catalog, catalog_payload
        ),
        monotonic=time.monotonic,
        sleep=time.sleep,
        get_task=run_task_detail,
        active_run_ledger_settings=lambda: default_settings().active_run_ledger,
        now_utc=lambda: datetime.now(UTC),
        start_audit_dependencies=lambda: _start_audit_dependencies(catalog, catalog_payload),
        catalog=catalog,
        catalog_payload=catalog_payload,
    )


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


def _start_audit_dependencies(
    catalog: AuditCatalog,
    catalog_payload: JsonObjectPayload,
) -> _audit_runs.StartAuditDependencies[AuditRunCreate]:
    return _audit_runs.StartAuditDependencies(
        make_audit_run_create=_make_audit_run_create,
        start_audit_run=run_start_audit_run,
        project_detail=run_project_detail,
        runbook=run_runbook,
        current_repo_active_runs=lambda repo_id: _merged_repo_active_runs(repo_id, catalog),
        record_started_run=lambda context: _report_workflows.record_started_run(
            context,
            dependencies=_report_workflow_dependencies(catalog, catalog_payload),
        ),
    )
