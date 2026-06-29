import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Literal, Never, TypedDict, cast

from enji_guard_cli.audits import REPORT_AUDITS, AuditAlias, AuditDefinition, AuditPayload
from enji_guard_cli.audits import audit_catalog as registry_audit_catalog
from enji_guard_cli.audits import audit_payload as registry_audit_payload
from enji_guard_cli.audits import require_report_audit as registry_require_report_audit
from enji_guard_cli.audits import resolve_audit as registry_resolve_audit
from enji_guard_cli.auth import AuthStatusPayload
from enji_guard_cli.auth import auth_status as run_auth_status
from enji_guard_cli.auth import auth_status_async as run_auth_status_async
from enji_guard_cli.enji_api import (
    REPORTS_LIST_DEFAULT_MIN_SEVERITY,
    REPORTS_LIST_DEFAULT_SELECTOR,
    REPORTS_LIST_DEFAULT_STALE,
    AccessPayload,
    AuditRunCreate,
    JsonObjectPayload,
    JsonValue,
    ReportsListPayload,
    RepoTransfer,
)
from enji_guard_cli.enji_api import access as run_access
from enji_guard_cli.enji_api import access_async as run_access_async
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
from enji_guard_cli.enji_api import reports_list as run_reports_list
from enji_guard_cli.enji_api import reports_list_async as run_reports_list_async
from enji_guard_cli.enji_api import runbook as run_runbook
from enji_guard_cli.enji_api import start_audit_run as run_start_audit_run
from enji_guard_cli.enji_api import update_repo_connection as run_update_repo_connection
from enji_guard_cli.errors import EnjiApiError

type OperationResult = object | Awaitable[object]
type OperationExecutor = Callable[..., OperationResult]
type RepoSort = Literal["default", "name", "weakest", "overall", "latest-report"]
type ScheduleFrequency = Literal["daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"]

OWNER_NAME_SLUG_PARTS = 2
REPORT_ARTIFACT_SCHEMA = "upfront.audit.summary"
RECON_REPORT_SCHEMA = "upfront.recon.report"
AUDIT_REPORT_SCHEMA = "upfront.audit.report"
DEFAULT_EXECUTION_FLOW = "single"
DEFAULT_FLOW_CONFIG: JsonObjectPayload = {}
DEFAULT_REPO_SORT: RepoSort = "default"
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "canceled", "cancelled", "skipped"})
WORKDAY_SCHEDULE_DAYS = ("mon", "tue", "wed", "thu", "fri")
ALL_SCHEDULE_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY: dict[ScheduleFrequency, tuple[str, ...]] = {
    "daily": ALL_SCHEDULE_DAYS,
    "workdays": WORKDAY_SCHEDULE_DAYS,
    "weekly-3x": ("mon", "wed", "fri"),
    "weekly-2x": ("mon", "thu"),
    "weekly": ("mon",),
    "monthly": ("mon",),
}
SCHEDULE_TIME_PARTS = 2
MAX_SCHEDULE_HOUR = 23
MAX_SCHEDULE_MINUTE = 59
SCORE_POOR_THRESHOLD = 40.0
SCORE_FAIR_THRESHOLD = 60.0
SCORE_GOOD_THRESHOLD = 75.0
SCORE_EXCELLENT_THRESHOLD = 90.0


class AuditRunBatchItem(TypedDict):
    audit: str
    action_key: str
    response: JsonObjectPayload


class AuditRunSkippedItem(TypedDict):
    audit: str
    action_key: str
    reason: str
    active_runs: list[JsonValue]
    current_head_sha: str | None
    last_audited_head_sha: str | None


class AuditRunBatchPayload(TypedDict):
    runs: list[AuditRunBatchItem]
    skipped: list[AuditRunSkippedItem]


class AuditRunSkippedPayload(TypedDict):
    skipped: bool
    audit: str
    action_key: str
    reason: str
    active_runs: list[JsonValue]


type ScoreGrade = Literal["critical", "poor", "fair", "good", "excellent"]


class ScoreSummaryPayload(TypedDict):
    overall_score: float | None
    overall_grade: ScoreGrade | None
    weakest_axis: str | None
    weakest_score: float | None
    weakest_grade: ScoreGrade | None


class RepoTargetPayload(TypedDict):
    project_id: str
    project_name: str | None
    repo_id: str
    github_owner: str | None
    github_name: str | None
    github_repo: str | None
    connected: bool | None
    recon_done: bool | None
    scores: JsonObjectPayload
    score_grades: dict[str, ScoreGrade]
    score_summary: ScoreSummaryPayload


class RepoResolvePayload(TypedDict):
    selector: str
    resolved: bool
    matches: list[RepoTargetPayload]


class ProjectRef(TypedDict):
    id: str
    name: str | None


type ReportAuditState = Literal["missing", "ready", "running"]


class ReportAuditStatusPayload(TypedDict):
    audit: str
    label: str
    action_key: str
    route_slug: str
    state: ReportAuditState
    ready: bool
    running: bool
    fleet_task_id: str | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None
    run_status: str | None
    current_head_sha: str | None
    last_audited_head_sha: str | None
    out_of_date: bool | None


class ReportStatusPayload(TypedDict):
    repo_id: str
    current_head_sha: str | None
    last_report_at: str | None
    complete: bool
    ready: list[str]
    running: list[str]
    missing: list[str]
    reports: list[ReportAuditStatusPayload]


class ReportReadItemPayload(TypedDict):
    audit: str
    current_head_sha: str | None
    last_audited_head_sha: str | None
    out_of_date: bool | None
    snapshot: JsonObjectPayload


class ReportReadPayload(TypedDict):
    reports: list[ReportReadItemPayload]


class RepoStatusPayload(TypedDict):
    repo_id: str
    active_run_count: int
    active_runs: list[JsonValue]
    rerun_state: JsonObjectPayload
    current_head_sha: str | None
    reports: ReportStatusPayload


class RepoRuntimeStatusPayload(TypedDict):
    project_id: str
    project_name: str | None
    repo_id: str
    github_owner: str | None
    github_name: str | None
    github_repo: str | None
    connected: bool | None
    recon_done: bool | None
    scores: JsonObjectPayload
    score_grades: dict[str, ScoreGrade]
    score_summary: ScoreSummaryPayload
    active_run_count: int
    active_runs: list[JsonValue]
    current_head_sha: str | None
    last_report_at: str | None
    reports: ReportStatusPayload


class ProjectRuntimeStatusPayload(TypedDict):
    project_id: str
    project_name: str | None
    repos: list[RepoRuntimeStatusPayload]


class RepoStatusSummaryPayload(TypedDict):
    project_count: int
    repo_count: int
    connected_repo_count: int
    active_run_count: int
    recon_done_count: int
    report_complete_count: int


class RepoStatusAllPayload(TypedDict):
    observed_at: str
    summary: RepoStatusSummaryPayload
    projects: list[ProjectRuntimeStatusPayload]


class AuditWaitPayload(TypedDict):
    repo_id: str
    audit: str | None
    idle: bool
    elapsed_seconds: int
    active_runs: list[JsonValue]


class OperationName(StrEnum):
    CATALOG_AUDITS = "catalog_audits"
    CATALOG_AUDIT = "catalog_audit"
    ACCESS = "access"
    REPORTS_LIST = "reports_list"
    AUTH_STATUS = "auth_status"


class OperationPayload(TypedDict):
    name: str
    cli_command: str
    mcp_tool: str
    summary: str


@dataclass(frozen=True, slots=True)
class OperationSpec:
    name: OperationName
    cli_command: str
    mcp_tool: str
    summary: str
    execute: OperationExecutor


@dataclass(frozen=True, slots=True)
class ScheduleUpdate:
    enabled: bool
    auto_fix: bool
    frequency: ScheduleFrequency
    days_of_week: list[str]
    schedule_time: str
    timezone: str


@dataclass(frozen=True, slots=True)
class ScheduleSettingsUpdate:
    enabled: bool | None
    frequency: ScheduleFrequency | None
    days_of_week: list[str] | None
    schedule_time: str | None
    timezone: str | None


@dataclass(frozen=True, slots=True)
class EmailPreferenceUpdate:
    manual_run_completion: bool | None
    scheduled_run_completion: bool | None


def _catalog_audits_operation() -> list[AuditPayload]:
    return registry_audit_catalog()


def _catalog_audit_operation(audit: AuditAlias) -> AuditPayload:
    return registry_audit_payload(registry_resolve_audit(audit))


def _access_operation() -> AccessPayload:
    return run_access()


def _reports_list_operation(
    selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    stale: bool = REPORTS_LIST_DEFAULT_STALE,
    min_severity: str | None = REPORTS_LIST_DEFAULT_MIN_SEVERITY,
) -> ReportsListPayload:
    return run_reports_list(selector=selector, stale=stale, min_severity=min_severity)


async def _reports_list_async_operation(
    selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    stale: bool = REPORTS_LIST_DEFAULT_STALE,
    min_severity: str | None = REPORTS_LIST_DEFAULT_MIN_SEVERITY,
) -> ReportsListPayload:
    return await run_reports_list_async(selector=selector, stale=stale, min_severity=min_severity)


def _auth_status_operation(auth_file: Path | None = None) -> AuthStatusPayload:
    return run_auth_status(auth_file)


async def auth_status_async_operation(auth_file: Path | None = None) -> AuthStatusPayload:
    return await run_auth_status_async(auth_file)


OPERATION_SPECS: tuple[OperationSpec, ...] = (
    OperationSpec(
        name=OperationName.CATALOG_AUDITS,
        cli_command="catalog audits",
        mcp_tool="enji_catalog_audits",
        summary="List the canonical Enji Guard audit catalog.",
        execute=_catalog_audits_operation,
    ),
    OperationSpec(
        name=OperationName.CATALOG_AUDIT,
        cli_command="catalog audit",
        mcp_tool="enji_catalog_audit",
        summary="Resolve one canonical Enji Guard audit alias.",
        execute=_catalog_audit_operation,
    ),
    OperationSpec(
        name=OperationName.ACCESS,
        cli_command="access",
        mcp_tool="enji_access",
        summary="Return Enji Guard plan, limits, and schedule access metadata.",
        execute=_access_operation,
    ),
    OperationSpec(
        name=OperationName.REPORTS_LIST,
        cli_command="report list",
        mcp_tool="enji_reports_list",
        summary="List compact Enji Guard report inventory across repositories.",
        execute=_reports_list_operation,
    ),
    OperationSpec(
        name=OperationName.AUTH_STATUS,
        cli_command="auth status",
        mcp_tool="enji_auth_status",
        summary="Report whether stored Enji Guard credentials are authenticated.",
        execute=_auth_status_operation,
    ),
)

_OPERATION_BY_NAME: dict[OperationName, OperationSpec] = {spec.name: spec for spec in OPERATION_SPECS}


def package_version() -> str:
    return version("enji-guard-cli")


async def _await_operation_result[T](result: Awaitable[T]) -> T:
    return await result


def resolve_operation_result[T](result: T | Awaitable[T]) -> T:
    if inspect.isawaitable(result):
        return asyncio.run(_await_operation_result(result))
    return result


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


def show_report_for_repo(repo: str, audit: AuditAlias, project: str | None) -> JsonObjectPayload:
    target = _resolve_single_repo_target(repo, project)
    return show_report(target["repo_id"], audit)


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
) -> JsonObjectPayload:
    patch = _email_preferences_patch(update)
    return _email_preferences_payload(
        [
            _email_preference_row(
                target,
                audit,
                run_put_audit_email_preferences(target["repo_id"], audit.action_key, patch),
            )
            for target in _selected_repo_targets(repo, project)
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
) -> JsonObjectPayload:
    _validate_schedule_settings_update(repo, project, update)
    rows = [
        _set_schedule_setting(target, audit, jobs, update)
        for target in _selected_repo_targets(repo, project)
        for jobs in (list_schedules(target["repo_id"]),)
        for audit in REPORT_AUDITS
    ]
    return _schedule_settings_payload(rows)


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


async def access_async_operation() -> AccessPayload:
    return await run_access_async()


async def reports_list_async_operation(
    selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    stale: bool = REPORTS_LIST_DEFAULT_STALE,
    min_severity: str | None = REPORTS_LIST_DEFAULT_MIN_SEVERITY,
) -> ReportsListPayload:
    return await _reports_list_async_operation(selector=selector, stale=stale, min_severity=min_severity)


def operation_payload(spec: OperationSpec) -> OperationPayload:
    return {
        "name": spec.name.value,
        "cli_command": spec.cli_command,
        "mcp_tool": spec.mcp_tool,
        "summary": spec.summary,
    }


def operation_catalog() -> list[OperationPayload]:
    return [operation_payload(spec) for spec in OPERATION_SPECS]


def resolve_operation_spec(name: OperationName) -> OperationSpec:
    spec = _OPERATION_BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"unknown operation name: {name}")
    return spec


def resolve_operation(name: OperationName) -> OperationPayload:
    return operation_payload(resolve_operation_spec(name))


def _owner_name_from_slug(slug: str) -> tuple[str | None, str | None]:
    parts = slug.split("/")
    if len(parts) < OWNER_NAME_SLUG_PARTS:
        return None, None
    owner = parts[0]
    name = parts[1]
    if not owner or not name:
        return None, None
    return owner, name


def _json_object_payload(payload: object) -> JsonObjectPayload:
    if not isinstance(payload, dict):
        raise ValueError("schedule payload must be a JSON object")
    normalized: JsonObjectPayload = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise ValueError("schedule payload keys must be strings")
        normalized[key] = _json_value(value)
    return normalized


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return _json_object_payload(value)
    raise ValueError("schedule payload contains a non-JSON value")


def _report_status_from_task_links(
    repo_id: str,
    payload: JsonObjectPayload,
    active_runs: list[JsonValue],
    rerun_state: JsonObjectPayload | None,
) -> ReportStatusPayload:
    links_by_action = _report_links_by_action(payload)
    active_runs_by_action = _active_runs_by_action(active_runs)
    current_head_sha = _current_head_sha(rerun_state)
    reports = [
        _report_audit_status(audit, links_by_action, active_runs_by_action, current_head_sha, rerun_state)
        for audit in REPORT_AUDITS
    ]
    last_report_at = _last_report_at(reports)
    ready = [report["audit"] for report in reports if report["ready"]]
    running = [report["audit"] for report in reports if report["running"]]
    missing = [report["audit"] for report in reports if report["state"] == "missing"]
    return {
        "repo_id": repo_id,
        "current_head_sha": current_head_sha,
        "last_report_at": last_report_at,
        "complete": not running and not missing,
        "ready": ready,
        "running": running,
        "missing": missing,
        "reports": reports,
    }


def _report_links_by_action(payload: JsonObjectPayload) -> dict[str, dict[str, JsonValue]]:
    links_by_action: dict[str, dict[str, JsonValue]] = {}
    for link in _json_object_list(payload.get("links")):
        action_key = _json_str(link.get("actionKey"))
        artifact_schema = _json_str(link.get("artifactSchemaName"))
        if action_key is not None and artifact_schema == REPORT_ARTIFACT_SCHEMA:
            links_by_action[action_key] = link
    return links_by_action


def _report_audit_status(
    audit: AuditDefinition,
    links_by_action: dict[str, dict[str, JsonValue]],
    active_runs_by_action: dict[str, dict[str, JsonValue]],
    current_head_sha: str | None,
    rerun_state: JsonObjectPayload | None,
) -> ReportAuditStatusPayload:
    action_key = audit.action_key
    link = links_by_action.get(action_key)
    active_run = active_runs_by_action.get(action_key)
    state = _report_audit_state(link, active_run)
    last_audited_head_sha = _last_audited_head_sha(rerun_state, action_key)
    route_slug = audit.route_slug
    if route_slug is None:
        raise ValueError("report audit status cannot be built for recon")
    return {
        "audit": audit.alias.value,
        "label": audit.label,
        "action_key": action_key,
        "route_slug": route_slug,
        "state": state,
        "ready": state == "ready",
        "running": state == "running",
        "fleet_task_id": _active_run_value(active_run, "fleetTaskId") or _link_value(link, "fleetTaskId"),
        "created_at": _active_run_value(active_run, "createdAt") or _link_value(link, "createdAt"),
        "started_at": _active_run_value(active_run, "startedAt") or _link_value(link, "startedAt"),
        "completed_at": _active_run_value(active_run, "completedAt") or _link_value(link, "completedAt"),
        "run_status": _active_run_value(active_run, "status") or _link_value(link, "status"),
        "current_head_sha": current_head_sha,
        "last_audited_head_sha": last_audited_head_sha,
        "out_of_date": _out_of_date(current_head_sha, last_audited_head_sha),
    }


def _current_head_sha(rerun_state: JsonObjectPayload | None) -> str | None:
    if rerun_state is None:
        return None
    state = _json_dict(rerun_state.get("state"))
    return _json_str(state.get("currentHeadSha"))


def _last_audited_head_sha(rerun_state: JsonObjectPayload | None, action_key: str) -> str | None:
    if rerun_state is None:
        return None
    state = _json_dict(rerun_state.get("state"))
    actions = _json_dict(state.get("actions"))
    action = _json_dict(actions.get(action_key))
    return _json_str(action.get("lastAuditedHeadSha"))


def _out_of_date(current_head_sha: str | None, last_audited_head_sha: str | None) -> bool | None:
    if current_head_sha is None or last_audited_head_sha is None:
        return None
    return current_head_sha != last_audited_head_sha


def _report_audit_state(
    link: dict[str, JsonValue] | None,
    active_run: dict[str, JsonValue] | None,
) -> ReportAuditState:
    if active_run is not None:
        return "running"
    if link is not None:
        return "ready"
    return "missing"


def _link_value(link: dict[str, JsonValue] | None, key: str) -> str | None:
    if link is None:
        return None
    return _json_str(link.get(key))


def _active_run_value(active_run: dict[str, JsonValue] | None, key: str) -> str | None:
    if active_run is None:
        return None
    return _json_str(active_run.get(key))


def _watched_active_runs(payload: JsonObjectPayload, action_key: str | None) -> list[JsonValue]:
    active_runs = _current_active_runs(payload)
    if action_key is None:
        return active_runs
    return _active_runs_for_action(active_runs, action_key)


def _active_runs_for_action(active_runs: list[JsonValue], action_key: str) -> list[JsonValue]:
    return [run for run in active_runs if _active_run_matches_action(run, action_key)]


def _active_run_matches_action(run: JsonValue, action_key: str) -> bool:
    if not isinstance(run, dict):
        return False
    return _json_str(run.get("actionKey")) == action_key or _nested_action_key_matches(run, action_key)


def _nested_action_key_matches(run: dict[str, JsonValue], action_key: str) -> bool:
    task = run.get("task")
    if not isinstance(task, dict):
        return False
    return _json_str(task.get("actionKey")) == action_key


def _action_key_for_optional_audit(audit: AuditAlias | None) -> str | None:
    if audit is None:
        return None
    return registry_resolve_audit(audit).action_key


def _audit_wait_payload(
    repo_id: str,
    audit: AuditAlias | None,
    idle: bool,
    started_at: float,
    active_runs: list[JsonValue],
) -> AuditWaitPayload:
    return {
        "repo_id": repo_id,
        "audit": audit.value if audit is not None else None,
        "idle": idle,
        "elapsed_seconds": round(time.monotonic() - started_at),
        "active_runs": active_runs,
    }


def _validate_wait_options(poll_seconds: int, timeout_seconds: int) -> None:
    if poll_seconds < 1:
        raise ValueError("poll_seconds must be at least 1")
    if timeout_seconds < poll_seconds:
        raise ValueError("timeout_seconds must be greater than or equal to poll_seconds")


def _next_poll_sleep(deadline: float, poll_seconds: int) -> float:
    return max(0.0, min(float(poll_seconds), deadline - time.monotonic()))


def _json_list(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _json_object_list(value: JsonValue | None) -> list[dict[str, JsonValue]]:
    return [item for item in _json_list(value) if isinstance(item, dict)]


def _json_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _json_bool(value: JsonValue | None) -> bool | None:
    return value if isinstance(value, bool) else None


def _json_dict(value: JsonValue | None) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _score_grade(score: float) -> ScoreGrade:
    if score < SCORE_POOR_THRESHOLD:
        return "critical"
    if score < SCORE_FAIR_THRESHOLD:
        return "poor"
    if score < SCORE_GOOD_THRESHOLD:
        return "fair"
    if score < SCORE_EXCELLENT_THRESHOLD:
        return "good"
    return "excellent"


def _score_number(value: JsonValue) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _numeric_scores(scores: JsonObjectPayload) -> list[tuple[str, float]]:
    numeric_scores: list[tuple[str, float]] = []
    for axis, value in scores.items():
        score = _score_number(value)
        if score is not None:
            numeric_scores.append((axis, score))
    return numeric_scores


def _score_grades(scores: JsonObjectPayload) -> dict[str, ScoreGrade]:
    return {axis: _score_grade(score) for axis, score in _numeric_scores(scores)}


def _score_summary(scores: JsonObjectPayload) -> ScoreSummaryPayload:
    numeric_scores = _numeric_scores(scores)
    if not numeric_scores:
        return {
            "overall_score": None,
            "overall_grade": None,
            "weakest_axis": None,
            "weakest_score": None,
            "weakest_grade": None,
        }

    weakest_axis, weakest_score = min(numeric_scores, key=lambda item: item[1])
    overall_score = round(sum(score for _, score in numeric_scores) / len(numeric_scores), 1)
    return {
        "overall_score": overall_score,
        "overall_grade": _score_grade(overall_score),
        "weakest_axis": weakest_axis,
        "weakest_score": weakest_score,
        "weakest_grade": _score_grade(weakest_score),
    }


def _sort_project_repos(projects: list[ProjectRuntimeStatusPayload], sort: RepoSort) -> None:
    if sort == "default":
        return
    for project in projects:
        if sort == "name":
            project["repos"].sort(key=_repo_name_sort_key)
        elif sort == "weakest":
            project["repos"].sort(key=_repo_weakest_sort_key)
        elif sort == "overall":
            project["repos"].sort(key=_repo_overall_sort_key)
        elif sort == "latest-report":
            project["repos"].sort(key=_repo_latest_report_sort_key)
        else:
            raise ValueError(f"unknown repo sort: {sort}")


def _repo_name_sort_key(repo: RepoRuntimeStatusPayload) -> str:
    return (repo["github_repo"] or repo["repo_id"]).lower()


def _repo_weakest_sort_key(repo: RepoRuntimeStatusPayload) -> tuple[float, str]:
    return (_missing_last_score(repo["score_summary"]["weakest_score"]), _repo_name_sort_key(repo))


def _repo_overall_sort_key(repo: RepoRuntimeStatusPayload) -> tuple[float, str]:
    return (_missing_last_score(repo["score_summary"]["overall_score"]), _repo_name_sort_key(repo))


def _repo_latest_report_sort_key(repo: RepoRuntimeStatusPayload) -> tuple[bool, float, str]:
    timestamp = _report_timestamp(repo["last_report_at"])
    return (timestamp is None, -(timestamp or 0.0), _repo_name_sort_key(repo))


def _missing_last_score(score: float | None) -> float:
    return score if score is not None else float("inf")


def _last_report_at(reports: list[ReportAuditStatusPayload]) -> str | None:
    latest: tuple[float, str] | None = None
    for report in reports:
        for value in (report["completed_at"], report["started_at"], report["created_at"]):
            if value is None:
                continue
            timestamp = _report_timestamp(value)
            if timestamp is not None and (latest is None or timestamp > latest[0]):
                latest = (timestamp, value)
    return latest[1] if latest is not None else None


def _report_timestamp(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


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
        _raise_bad_selector(f"project selector matched no projects: {project}")
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


def _project_ref_matches(project_ref: ProjectRef, selector: str) -> bool:
    return project_ref["id"] == selector or project_ref["name"] == selector


def _ambiguous_project_message(project_refs: list[ProjectRef]) -> str:
    candidates = ", ".join(_project_candidate(project_ref) for project_ref in project_refs)
    return f"project selector is ambiguous; pass --project. candidates: {candidates}"


def _project_candidate(project_ref: ProjectRef) -> str:
    name = project_ref["name"]
    if name is None:
        return str(project_ref["id"])
    return f"{name} ({project_ref['id']})"


def _validated_project_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("project name must not be empty")
    return normalized


def _transfer_schedule_replacements(preflight: JsonObjectPayload) -> JsonObjectPayload | None:
    replacements = preflight.get("scheduleReplacements")
    if isinstance(replacements, dict):
        return cast(JsonObjectPayload, replacements)
    return None


def _parse_github_repo(github_repo: str) -> tuple[str, str]:
    owner, name = _owner_name_from_slug(github_repo)
    if owner is None or name is None:
        raise ValueError("repo must be an owner/name GitHub slug")
    return owner, name


def _selected_repo_targets(repo: str | None, project: str | None) -> list[RepoTargetPayload]:
    if repo is not None:
        return [_resolve_single_repo_target(repo, project)]
    return [target for project_id in _selected_project_ids(project) for target in _project_repo_targets(project_id)]


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


def _repo_target_matches(target: RepoTargetPayload, selector: str) -> bool:
    return selector == target["repo_id"] or selector == target["github_repo"]


def _ambiguous_repo_message(selector: str, matches: list[RepoTargetPayload]) -> str:
    candidates = ", ".join(_repo_candidate(match) for match in matches)
    return f"repo selector is ambiguous: {selector}. candidates: {candidates}"


def _repo_candidate(target: RepoTargetPayload) -> str:
    project_name = target["project_name"] or target["project_id"]
    github_repo = target["github_repo"] or target["repo_id"]
    return f"{github_repo} in {project_name} ({target['repo_id']})"


def _repo_target(
    project_id: str,
    project_name: str | None,
    repo: dict[str, JsonValue],
) -> RepoTargetPayload:
    repo_id = _required_str(repo, "id", "repo is missing id")
    owner = _json_str(repo.get("githubOwner"))
    name = _json_str(repo.get("githubName"))
    scores = _json_dict(repo.get("scores"))
    return {
        "project_id": project_id,
        "project_name": project_name,
        "repo_id": repo_id,
        "github_owner": owner,
        "github_name": name,
        "github_repo": f"{owner}/{name}" if owner is not None and name is not None else None,
        "connected": _json_bool(repo.get("connected")),
        "recon_done": _json_bool(repo.get("reconDone")),
        "scores": scores,
        "score_grades": _score_grades(scores),
        "score_summary": _score_summary(scores),
    }


def _targeted_run_payload(target: RepoTargetPayload, payload: object) -> dict[str, object]:
    if isinstance(payload, dict):
        result: dict[str, object] = {"target": target}
        result.update(payload)
        return result
    return {"target": target, "result": payload}


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


def _empty_report_status(repo_id: str) -> ReportStatusPayload:
    reports = [_empty_report_audit_status(audit) for audit in REPORT_AUDITS]
    return {
        "repo_id": repo_id,
        "current_head_sha": None,
        "last_report_at": None,
        "complete": False,
        "ready": [],
        "running": [],
        "missing": [report["audit"] for report in reports],
        "reports": reports,
    }


def _empty_report_audit_status(audit: AuditDefinition) -> ReportAuditStatusPayload:
    route_slug = audit.route_slug
    if route_slug is None:
        raise ValueError("report audit status cannot be built for recon")
    return {
        "audit": audit.alias.value,
        "label": audit.label,
        "action_key": audit.action_key,
        "route_slug": route_slug,
        "state": "missing",
        "ready": False,
        "running": False,
        "fleet_task_id": None,
        "created_at": None,
        "started_at": None,
        "completed_at": None,
        "run_status": None,
        "current_head_sha": None,
        "last_audited_head_sha": None,
        "out_of_date": None,
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


def _current_active_runs(payload: JsonObjectPayload) -> list[JsonValue]:
    return [run for run in _json_list(payload.get("activeRuns")) if _run_is_active(run)]


def _active_runs_by_action(active_runs: list[JsonValue]) -> dict[str, dict[str, JsonValue]]:
    runs_by_action: dict[str, dict[str, JsonValue]] = {}
    for run in active_runs:
        if not isinstance(run, dict):
            continue
        action_key = _json_str(run.get("actionKey"))
        if action_key is None:
            continue
        runs_by_action.setdefault(action_key, run)
    return runs_by_action


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


def _email_preferences_patch(update: EmailPreferenceUpdate) -> JsonObjectPayload:
    patch: JsonObjectPayload = {}
    if update.manual_run_completion is not None:
        patch["manualRunCompletion"] = update.manual_run_completion
    if update.scheduled_run_completion is not None:
        patch["scheduledRunCompletion"] = update.scheduled_run_completion
    if not patch:
        raise ValueError("pass --manual or --auto")
    return patch


def _email_preference_row(
    target: RepoTargetPayload,
    audit: AuditDefinition,
    payload: JsonObjectPayload,
) -> dict[str, JsonValue]:
    resolved = _json_dict(payload.get("resolved"))
    return {
        "project_id": target["project_id"],
        "project_name": target["project_name"],
        "repo_id": target["repo_id"],
        "github_repo": target["github_repo"],
        "audit": audit.alias.value,
        "action_key": audit.action_key,
        "manual_run_completion": _json_bool(resolved.get("manualRunCompletion")),
        "scheduled_run_completion": _json_bool(resolved.get("scheduledRunCompletion")),
    }


def _email_preferences_payload(rows: list[dict[str, JsonValue]]) -> JsonObjectPayload:
    preferences = [cast(JsonValue, row) for row in rows]
    return {
        "preferences": preferences,
        "summary": {
            "repo_count": _email_preference_repo_count(rows),
            "audit_count": len(rows),
        },
    }


def _email_preference_repo_count(rows: list[dict[str, JsonValue]]) -> int:
    return len({repo_id for row in rows if isinstance(repo_id := row.get("repo_id"), str)})


def _validate_schedule_settings_update(
    repo: str | None,
    project: str | None,
    update: ScheduleSettingsUpdate,
) -> None:
    if repo is None and project is None:
        raise ValueError("schedule set without REPO requires --project")
    if (
        update.enabled is None
        and update.frequency is None
        and update.days_of_week is None
        and update.schedule_time is None
    ):
        raise ValueError("pass --enabled or --freq")
    if update.days_of_week is not None and update.frequency is None:
        raise ValueError("pass --freq when overriding --day")
    if update.timezone is not None and update.schedule_time is None:
        raise ValueError("pass --at when setting timezone")


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


def _schedule_settings_payload_for_job(
    existing: JsonObjectPayload | None,
    update: ScheduleSettingsUpdate,
) -> JsonObjectPayload | None:
    if existing is None and update.enabled is not True:
        return None
    existing_frequency = _json_str(existing.get("frequency")) if existing is not None else None
    frequency = _schedule_frequency(update.frequency or existing_frequency)
    if frequency is None:
        frequency = "weekly"
    schedule_time = _selected_schedule_time(existing, update)
    existing_timezone = _json_str(existing.get("timezone")) if existing is not None else None
    timezone = update.timezone or existing_timezone
    base = dict(existing) if existing is not None else {}
    desired = {
        **base,
        **_schedule_payload(
            ScheduleUpdate(
                enabled=_schedule_enabled(existing, update),
                auto_fix=_schedule_auto_fix(existing),
                frequency=frequency,
                days_of_week=_selected_schedule_days(existing, update, frequency),
                schedule_time=schedule_time,
                timezone=timezone or "UTC",
            )
        ),
        "autofixVariantKey": _json_str(base.get("autofixVariantKey")) or "default",
    }
    if update.schedule_time == "auto":
        desired.pop("scheduleTime", None)
    return desired


def _schedule_enabled(existing: JsonObjectPayload | None, update: ScheduleSettingsUpdate) -> bool:
    if update.enabled is not None:
        return update.enabled
    if existing is None:
        return False
    return _json_bool(existing.get("enabled")) is True


def _schedule_auto_fix(existing: JsonObjectPayload | None) -> bool:
    if existing is None:
        return False
    return _json_bool(existing.get("autoFix")) is True


def _selected_schedule_days(
    existing: JsonObjectPayload | None,
    update: ScheduleSettingsUpdate,
    frequency: ScheduleFrequency,
) -> list[str]:
    if update.days_of_week is not None:
        return update.days_of_week
    if update.frequency is not None or existing is None:
        return list(DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY[frequency])
    return _json_list_of_str(existing.get("daysOfWeek")) or list(DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY[frequency])


def _selected_schedule_time(existing: JsonObjectPayload | None, update: ScheduleSettingsUpdate) -> str:
    if update.schedule_time is not None:
        return update.schedule_time
    if existing is not None and _json_str(existing.get("scheduleTimeSource")) == "user":
        return _json_str(existing.get("scheduleTime")) or "auto"
    return "auto"


def _schedule_setting_row(
    target: RepoTargetPayload,
    audit: AuditDefinition,
    job: JsonObjectPayload | None,
    *,
    changed: bool | None = None,
    status: str | None = None,
) -> dict[str, JsonValue]:
    row: dict[str, JsonValue] = {
        "project_id": target["project_id"],
        "project_name": target["project_name"],
        "repo_id": target["repo_id"],
        "github_repo": target["github_repo"],
        "audit": audit.alias.value,
        "job_kind": audit.job_kind,
        "configured": job is not None,
        "enabled": False,
        "frequency": None,
        "days_of_week": [],
        "schedule_time": None,
        "schedule_time_source": None,
        "timezone": None,
        "auto_fix": False,
    }
    if job is not None:
        row.update(_schedule_setting_fields(job))
    if changed is not None:
        row["changed"] = changed
    if status is not None:
        row["status"] = status
    return row


def _schedule_setting_fields(job: JsonObjectPayload) -> dict[str, JsonValue]:
    schedule_time_source = _json_str(job.get("scheduleTimeSource"))
    return {
        "enabled": _json_bool(job.get("enabled")) is True,
        "frequency": _schedule_frequency(_json_str(job.get("frequency"))),
        "days_of_week": _json_str_values(_json_list_of_str(job.get("daysOfWeek"))),
        "schedule_time": _json_str(job.get("scheduleTime")),
        "schedule_time_source": schedule_time_source,
        "timezone": _json_str(job.get("timezone")),
        "auto_fix": _json_bool(job.get("autoFix")) is True,
    }


def _schedule_settings_payload(rows: list[dict[str, JsonValue]]) -> JsonObjectPayload:
    schedules = [cast(JsonValue, row) for row in rows]
    return {
        "schedules": schedules,
        "summary": {
            "repo_count": _email_preference_repo_count(rows),
            "audit_count": len(rows),
            "enabled_count": sum(1 for row in rows if row.get("enabled") is True),
            "changed_count": sum(1 for row in rows if row.get("changed") is True),
            "unchanged_count": sum(1 for row in rows if row.get("changed") is False),
        },
    }


def _schedule_effective_state(job: JsonObjectPayload) -> JsonObjectPayload:
    return {
        "enabled": _json_bool(job.get("enabled")) is True,
        "autoFix": _json_bool(job.get("autoFix")) is True,
        "autofixVariantKey": _json_str(job.get("autofixVariantKey")) or "default",
        "frequency": _schedule_frequency(_json_str(job.get("frequency"))) or "weekly",
        "daysOfWeek": _json_str_values(_json_list_of_str(job.get("daysOfWeek"))),
        "scheduleTime": _json_str(job.get("scheduleTime")),
        "scheduleTimeSource": _json_str(job.get("scheduleTimeSource")) or "auto",
        "timezone": _json_str(job.get("timezone")) or "UTC",
    }


def _schedule_payload(
    update: ScheduleUpdate,
) -> JsonObjectPayload:
    _validate_days_of_week(update.days_of_week)
    _validate_days_for_frequency(update.frequency, update.days_of_week)
    payload: JsonObjectPayload = {
        "enabled": update.enabled,
        "autoFix": update.auto_fix,
        "autofixVariantKey": "default",
        "frequency": update.frequency,
        "daysOfWeek": _json_str_values(update.days_of_week),
        "scheduleTimeSource": "auto" if update.schedule_time == "auto" else "user",
        "timezone": update.timezone,
    }
    if update.schedule_time != "auto":
        payload["scheduleTime"] = _validated_schedule_time(update.schedule_time)
    return payload


def _schedule_job_by_kind(payload: JsonObjectPayload, job_kind: str | None) -> JsonObjectPayload | None:
    if job_kind is None:
        raise ValueError("recon does not have a schedulable improvement job")
    for job in _json_object_list(payload.get("jobs")):
        if _json_str(job.get("kind")) == job_kind:
            return job
    return None


def _validate_days_of_week(days_of_week: list[str]) -> None:
    if not days_of_week:
        raise ValueError("days_of_week must not be empty")
    invalid_days = [day for day in days_of_week if day not in ALL_SCHEDULE_DAYS]
    if invalid_days:
        raise ValueError(f"unknown day(s): {', '.join(invalid_days)}")
    duplicate_days = sorted({day for day in days_of_week if days_of_week.count(day) > 1})
    if duplicate_days:
        raise ValueError(f"duplicate day(s): {', '.join(duplicate_days)}")


def _validate_days_for_frequency(frequency: ScheduleFrequency, days_of_week: list[str]) -> None:
    expected_count = len(DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY[frequency])
    if len(days_of_week) != expected_count:
        raise ValueError(f"{frequency} expects {expected_count} day(s)")


def _schedule_frequency(value: str | None) -> ScheduleFrequency | None:
    if value in DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY:
        return cast(ScheduleFrequency, value)
    return None


def _validated_schedule_time(value: str) -> str:
    parts = value.split(":", 1)
    if len(parts) != SCHEDULE_TIME_PARTS:
        raise ValueError("schedule time must be auto or HH:MM")
    hour, minute = parts
    if not hour.isdigit() or not minute.isdigit():
        raise ValueError("schedule time must be auto or HH:MM")
    hour_int = int(hour)
    minute_int = int(minute)
    if hour_int > MAX_SCHEDULE_HOUR or minute_int > MAX_SCHEDULE_MINUTE:
        raise ValueError("schedule time must be auto or HH:MM")
    return f"{hour_int:02d}:{minute_int:02d}"


def _json_list_of_str(value: JsonValue | None) -> list[str]:
    return [item for item in _json_list(value) if isinstance(item, str)]


def _json_str_values(values: list[str]) -> list[JsonValue]:
    json_values: list[JsonValue] = []
    json_values.extend(values)
    return json_values


def _raise_bad_selector(message: str) -> Never:
    raise EnjiApiError("BAD_SELECTOR", message)


def _run_is_active(run: JsonValue) -> bool:
    if not isinstance(run, dict):
        return False
    if _json_str(run.get("completedAt")) is not None:
        return False
    status = _json_str(run.get("status"))
    return status not in TERMINAL_RUN_STATUSES


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


def _project_repo(project: JsonObjectPayload, repo_id: str) -> dict[str, JsonValue]:
    for repo in _json_object_list(project.get("repos")):
        if _json_str(repo.get("id")) == repo_id:
            _ensure_repo_connected(repo)
            return repo
    raise ValueError(f"project does not contain repo id: {repo_id}")


def _ensure_repo_connected(repo: dict[str, JsonValue]) -> None:
    if repo.get("connected") is not True:
        raise ValueError(f"repo is not connected: {_repo_full_name(repo)}")


def _catalog_action(catalog: JsonObjectPayload, action_key: str) -> dict[str, JsonValue]:
    for action in _json_object_list(catalog.get("curatedActions")):
        if _json_str(action.get("actionKey")) == action_key:
            return action
    raise ValueError(f"catalog does not contain action key: {action_key}")


def _repo_full_name(repo: dict[str, JsonValue]) -> str:
    owner = _required_str(repo, "githubOwner", "repo is missing githubOwner")
    name = _required_str(repo, "githubName", "repo is missing githubName")
    return f"{owner}/{name}"


def _action_title(action: dict[str, JsonValue]) -> str:
    return _required_str(action, "title", "curated action is missing title")


def _task_description(
    action: dict[str, JsonValue],
    repo: dict[str, JsonValue],
    web_resources: list[dict[str, JsonValue]],
) -> str:
    template = _json_str(action.get("taskDescriptionTemplate")) or _default_task_description_template()
    variables = _task_description_variables(action, repo, web_resources)
    for name, value in variables.items():
        template = template.replace(f"{{{{{name}}}}}", value)
    return template


def _task_description_variables(
    action: dict[str, JsonValue],
    repo: dict[str, JsonValue],
    web_resources: list[dict[str, JsonValue]],
) -> dict[str, str]:
    repo_full_name = _repo_full_name(repo)
    return {
        "recurringPrefix": f"Task created from {_required_str(action, 'actionKey', 'actionKey is missing')} for {repo_full_name}.",
        "repoFullName": repo_full_name,
        "repoUrl": f"https://github.com/{repo_full_name}",
        "linkedSites": _linked_sites_markdown(web_resources),
        "artifactSchemaName": _required_str(
            action, "artifactSchemaName", "curated action is missing artifactSchemaName"
        ),
        "artifactSchemaVersion": _required_str(
            action, "artifactSchemaVersion", "curated action is missing artifactSchemaVersion"
        ),
        "reportSchemaName": _report_schema_name(action),
        "constraintsSection": "- use task title/description only",
        "pentestSection": "",
        "autofixSection": "",
    }


def _default_task_description_template() -> str:
    return (
        "{{recurringPrefix}}\n"
        "\n"
        "Repository:\n"
        "- full_name: {{repoFullName}}\n"
        "- url: {{repoUrl}}\n"
        "\n"
        "Linked websites:\n"
        "{{linkedSites}}\n"
        "\n"
        "Artifact contract for this run:\n"
        "- structured artifact metadata.schema_name={{artifactSchemaName}}\n"
        "- structured artifact metadata.schema_version={{artifactSchemaVersion}}\n"
        "- markdown report metadata.schema_name={{reportSchemaName}}\n"
        "- artifacts must remain machine-readable and deterministic\n"
        "\n"
        "Constraints:\n"
        "{{constraintsSection}}"
    )


def _report_schema_name(action: dict[str, JsonValue]) -> str:
    runbook_kind = _json_str(action.get("runbookKind"))
    if runbook_kind == "recon":
        return RECON_REPORT_SCHEMA
    return AUDIT_REPORT_SCHEMA


def _linked_web_resources(project: JsonObjectPayload, repo_id: str) -> list[dict[str, JsonValue]]:
    return [
        resource
        for resource in _json_object_list(project.get("webResources"))
        if _resource_links_repo(resource, repo_id)
    ]


def _resource_links_repo(resource: dict[str, JsonValue], repo_id: str) -> bool:
    return repo_id in [item for item in _json_list(resource.get("repoIds")) if isinstance(item, str)]


def _linked_sites_markdown(web_resources: list[dict[str, JsonValue]]) -> str:
    urls = [_json_str(resource.get("url")) for resource in web_resources]
    linked_urls = [url for url in urls if url is not None]
    if not linked_urls:
        return "- none linked yet"
    return "\n".join(f"- {url}" for url in linked_urls)


def _json_object_or_default(value: JsonValue | None) -> JsonObjectPayload:
    if not isinstance(value, dict):
        return dict(DEFAULT_FLOW_CONFIG)
    return value


def _required_str(payload: dict[str, JsonValue], key: str, message: str) -> str:
    value = _json_str(payload.get(key))
    if value is None:
        raise ValueError(message)
    return value
