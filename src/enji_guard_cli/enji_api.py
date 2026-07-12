from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast
from uuid import uuid4

from enji_guard_cli._enji_api_contract import (
    ACCESS_ENDPOINT_SPEC,
    AUDIT_AUTO_RUN_PUT_ENDPOINT_SPEC,
    AUDIT_AUTO_RUNS_ENDPOINT_SPEC,
    AUDIT_EMAIL_PREFERENCES_GET_ENDPOINT_SPEC,
    AUDIT_EMAIL_PREFERENCES_PUT_ENDPOINT_SPEC,
    CATALOG_ENDPOINT_SPEC,
    FLEET_PROJECT_CREATE_ENDPOINT_SPEC,
    FLEET_PROJECT_DELETE_ENDPOINT_SPEC,
    IMPROVEMENT_JOB_PUT_ENDPOINT_SPEC,
    IMPROVEMENT_JOBS_ENDPOINT_SPEC,
    PROJECT_DETAIL_ENDPOINT_SPEC,
    PROJECT_RENAME_ENDPOINT_SPEC,
    PROJECT_REPO_CONNECTION_ENDPOINT_SPEC,
    PROJECT_REPO_DELETE_ENDPOINT_SPEC,
    PROJECT_REPOS_ADD_ENDPOINT_SPEC,
    PROJECT_RUN_LANGUAGE_ENDPOINT_SPEC,
    PROJECTS_ENDPOINT_SPEC,
    REPO_ACTIVE_RUNS_ENDPOINT_SPEC,
    REPO_AUDIT_RERUN_STATE_ENDPOINT_SPEC,
    REPO_AUDIT_RUNS_ENDPOINT_SPEC,
    REPO_AUDIT_SUMMARY_ENDPOINT_SPEC,
    REPO_TASK_LINKS_ENDPOINT_SPEC,
    REPO_TRANSFER_ENDPOINT_SPEC,
    REPO_TRANSFER_PREFLIGHT_ENDPOINT_SPEC,
    REPORTS_LIST_ENDPOINT_SPEC,
    RUNBOOK_ENDPOINT_SPEC,
    TASK_DETAIL_ENDPOINT_SPEC,
    USER_PREFERENCES_GET_ENDPOINT_SPEC,
    USER_PREFERENCES_PUT_ENDPOINT_SPEC,
    UX_PROJECT_CREATE_ENDPOINT_SPEC,
    UX_PROJECT_DELETE_ENDPOINT_SPEC,
)
from enji_guard_cli.enji_api_impl.client import (
    ApiEndpoint,
    EnjiApiSession,
    run_api_no_content,
    run_api_request,
    run_api_request_async,
)
from enji_guard_cli.enji_api_impl.client import (
    load_api_session as _load_api_session_impl,
)
from enji_guard_cli.errors import EnjiApiError, EnjiPartialStateError, PartialStateDetails
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue
from enji_guard_cli.transport import EnjiHttpClient, EnjiHttpError, EnjiJsonValue

HTTP_OK = 200
HTTP_CREATED = 201
HTTP_NO_CONTENT = 204
REPORTS_LIST_DEFAULT_SELECTOR = "*"
REPORTS_LIST_DEFAULT_STALE = False
REPORTS_LIST_DEFAULT_MIN_SEVERITY: str | None = None
HTTP_CREATED_ONLY = frozenset({HTTP_CREATED})
HTTP_NO_CONTENT_ONLY = frozenset({HTTP_NO_CONTENT})
HTTP_OK_OR_NO_CONTENT = frozenset({HTTP_OK, HTTP_NO_CONTENT})

type JsonObjectParser[T] = Callable[[dict[str, object]], T]
type LanguageCode = Literal["en", "ru"]


class AccessLimitsPayload(TypedDict):
    can_add_repo: bool | None
    can_add_website: bool | None
    can_create_project: bool | None
    can_invite_members: bool | None
    can_run_one_shot_autofix: bool | None
    can_run_one_shot_pentest: bool | None
    can_use_schedules: bool | None
    audit_runs: dict[str, JsonValue]
    autofix_runs: dict[str, JsonValue]


class AccessPayload(TypedDict):
    group: str | None
    full_access: bool | None
    limits: AccessLimitsPayload
    usage: list[JsonValue]


class ProjectOverviewPayload(TypedDict):
    id: str | None
    name: str | None
    repo_ids: list[str]
    scores: dict[str, JsonValue]
    recon_pending: bool | None


class ReportsListPayload(TypedDict):
    projects: list[ProjectOverviewPayload]


@dataclass(frozen=True, slots=True)
class AuditRunCreate:
    repo_id: str
    project_id: str
    action_key: str
    fleet_task_body: JsonObjectPayload


@dataclass(frozen=True, slots=True)
class RepoTransfer:
    source_project_id: str
    repo_id: str
    target_project_id: str
    schedule_replacements: JsonObjectPayload | None = None


class FleetProjectCreateRequest(TypedDict):
    name: str


class FleetProjectCreateResponse(TypedDict):
    id: str


class UxProjectCreateRequest(TypedDict):
    fleetProjectId: str
    name: str
    createdAt: str


class ProjectPatchRequest(TypedDict, total=False):
    name: str


class UserLanguageUpdateRequest(TypedDict):
    language: LanguageCode


class RepoTransferPreflightRequest(TypedDict):
    targetProjectId: str


class RepoTransferRequest(TypedDict):
    targetProjectId: str
    scheduleReplacements: NotRequired[dict[str, JsonValue]]


class RepoAddRequest(TypedDict):
    githubOwner: str
    githubName: str


class RepoConnectionRequest(TypedDict):
    connected: bool
    lastVerifiedAt: str


class AuditRunCreateRequest(TypedDict):
    projectId: str
    actionKey: str
    fleetTaskBody: JsonObjectPayload
    clientRequestId: str


class AuditEmailPreferenceRequest(TypedDict, total=False):
    manualRunCompletion: bool
    scheduledRunCompletion: bool


def access(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AccessPayload:
    return run_api_request(
        auth_file,
        client,
        ACCESS_ENDPOINT.request(),
    )


async def access_async(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AccessPayload:
    return await run_api_request_async(
        auth_file,
        client,
        ACCESS_ENDPOINT.request(),
    )


def _parse_access_payload(payload: dict[str, object]) -> AccessPayload:
    access_payload = _as_dict(payload.get("access"))
    limits = _as_dict(access_payload.get("limits"))
    return {
        "group": _optional_str(access_payload.get("group")),
        "full_access": _optional_bool(access_payload.get("fullAccess")),
        "limits": {
            "can_add_repo": _optional_bool(limits.get("canAddRepo")),
            "can_add_website": _optional_bool(limits.get("canAddWebsite")),
            "can_create_project": _optional_bool(limits.get("canCreateProject")),
            "can_invite_members": _optional_bool(limits.get("canInviteMembers")),
            "can_run_one_shot_autofix": _optional_bool(limits.get("canRunOneShotAutofix")),
            "can_run_one_shot_pentest": _optional_bool(limits.get("canRunOneShotPentest")),
            "can_use_schedules": _optional_bool(limits.get("canUseSchedules")),
            "audit_runs": _normalize_json_object(limits.get("auditRuns")),
            "autofix_runs": _normalize_json_object(limits.get("autofixRuns")),
        },
        "usage": _normalize_json_list(access_payload.get("usage")),
    }


def reports_list(
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
    *,
    selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    stale: bool = REPORTS_LIST_DEFAULT_STALE,
    min_severity: str | None = REPORTS_LIST_DEFAULT_MIN_SEVERITY,
) -> ReportsListPayload:
    _validate_reports_filters(stale=stale, min_severity=min_severity)
    return run_api_request(
        auth_file,
        client,
        REPORTS_LIST_ENDPOINT.request(parser=_reports_list_parser(selector)),
    )


async def reports_list_async(
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
    *,
    selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    stale: bool = REPORTS_LIST_DEFAULT_STALE,
    min_severity: str | None = REPORTS_LIST_DEFAULT_MIN_SEVERITY,
) -> ReportsListPayload:
    _validate_reports_filters(stale=stale, min_severity=min_severity)
    return await run_api_request_async(
        auth_file,
        client,
        REPORTS_LIST_ENDPOINT.request(parser=_reports_list_parser(selector)),
    )


def projects(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> JsonObjectPayload:
    return run_api_request(auth_file, client, PROJECTS_ENDPOINT.request())


def project_detail(
    project_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        PROJECT_DETAIL_ENDPOINT.request(path_params={"projectId": project_id}),
    )


def user_preferences(
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(auth_file, client, USER_PREFERENCES_GET_ENDPOINT.request())


def put_user_language(
    language: LanguageCode,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    request: UserLanguageUpdateRequest = {"language": language}
    return run_api_request(
        auth_file,
        client,
        USER_PREFERENCES_PUT_ENDPOINT.request(json_body=cast(EnjiJsonValue, request)),
    )


def project_run_language(
    project_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        PROJECT_RUN_LANGUAGE_ENDPOINT.request(path_params={"projectId": project_id}),
    )


def create_project(
    name: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    fleet_request: FleetProjectCreateRequest = {"name": name}
    fleet_project = run_api_request(
        auth_file,
        client,
        FLEET_PROJECT_CREATE_ENDPOINT.request(json_body=cast(EnjiJsonValue, fleet_request)),
    )
    ux_request: UxProjectCreateRequest = {
        "fleetProjectId": fleet_project["id"],
        "name": name,
        "createdAt": datetime.now(UTC).isoformat(),
    }
    try:
        return run_api_request(
            auth_file,
            client,
            UX_PROJECT_CREATE_ENDPOINT.request(json_body=cast(EnjiJsonValue, ux_request)),
        )
    except EnjiApiError as exc:
        raise EnjiPartialStateError(
            PartialStateDetails(
                operation="create_project",
                completed_step="fleet_create",
                failed_step="ux_create",
                project_id=fleet_project["id"],
                project_name=name,
                upstream_code=exc.code,
                upstream_message=exc.message,
            )
        ) from exc


def rename_project(
    project_id: str,
    name: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    patch: ProjectPatchRequest = {"name": name}
    return run_api_request(
        auth_file,
        client,
        PROJECT_RENAME_ENDPOINT.request(path_params={"projectId": project_id}, json_body=cast(EnjiJsonValue, patch)),
    )


def delete_project(
    project_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> None:
    run_api_no_content(
        auth_file,
        client,
        UX_PROJECT_DELETE_ENDPOINT.request(path_params={"projectId": project_id}),
    )
    try:
        run_api_no_content(
            auth_file,
            client,
            FLEET_PROJECT_DELETE_ENDPOINT.request(path_params={"projectId": project_id}),
        )
    except EnjiApiError as exc:
        raise EnjiPartialStateError(
            PartialStateDetails(
                operation="delete_project",
                completed_step="ux_delete",
                failed_step="fleet_delete",
                project_id=project_id,
                upstream_code=exc.code,
                upstream_message=exc.message,
            )
        ) from exc


def preflight_repo_move(
    source_project_id: str,
    repo_id: str,
    target_project_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    request: RepoTransferPreflightRequest = {"targetProjectId": target_project_id}
    return run_api_no_content(
        auth_file,
        client,
        REPO_TRANSFER_PREFLIGHT_ENDPOINT.request(
            path_params={"sourceProjectId": source_project_id, "repoId": repo_id},
            json_body=cast(EnjiJsonValue, request),
        ),
    )


def move_repo(
    request: RepoTransfer,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    json_request: RepoTransferRequest = {"targetProjectId": request.target_project_id}
    if request.schedule_replacements is not None:
        json_request["scheduleReplacements"] = request.schedule_replacements
    return run_api_request(
        auth_file,
        client,
        REPO_TRANSFER_ENDPOINT.request(
            path_params={"sourceProjectId": request.source_project_id, "repoId": request.repo_id},
            json_body=cast(EnjiJsonValue, json_request),
        ),
    )


def catalog(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> JsonObjectPayload:
    return run_api_request(auth_file, client, CATALOG_ENDPOINT.request())


def runbook(
    runbook_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        RUNBOOK_ENDPOINT.request(path_params={"runbookId": runbook_id}),
    )


def add_project_repo(
    project_id: str,
    github_owner: str,
    github_name: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    request: RepoAddRequest = {"githubOwner": github_owner, "githubName": github_name}
    return run_api_request(
        auth_file,
        client,
        PROJECT_REPOS_ADD_ENDPOINT.request(
            path_params={"projectId": project_id},
            json_body=cast(EnjiJsonValue, request),
        ),
    )


def delete_project_repo(
    project_id: str,
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> None:
    run_api_no_content(
        auth_file,
        client,
        PROJECT_REPO_DELETE_ENDPOINT.request(path_params={"projectId": project_id, "repoId": repo_id}),
    )


def connect_project_repo(
    project_id: str,
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    request: RepoConnectionRequest = {
        "connected": True,
        "lastVerifiedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    return run_api_request(
        auth_file,
        client,
        PROJECT_REPO_CONNECTION_ENDPOINT.request(
            path_params={"projectId": project_id, "repoId": repo_id},
            json_body=cast(EnjiJsonValue, request),
        ),
    )


def repo_active_runs(
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        REPO_ACTIVE_RUNS_ENDPOINT.request(path_params={"repoId": repo_id}),
    )


def repo_audit_rerun_state(
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        REPO_AUDIT_RERUN_STATE_ENDPOINT.request(path_params={"repoId": repo_id}),
    )


def repo_task_links(
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        REPO_TASK_LINKS_ENDPOINT.request(path_params={"repoId": repo_id}),
    )


def task_detail(
    task_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        ApiEndpoint(
            spec=TASK_DETAIL_ENDPOINT_SPEC,
            parser=_parse_json_object_payload,
        ).request(path_params={"taskId": task_id}),
    )


def start_audit_run(
    request: AuditRunCreate,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    json_request: AuditRunCreateRequest = {
        "projectId": request.project_id,
        "actionKey": request.action_key,
        "fleetTaskBody": request.fleet_task_body,
        "clientRequestId": str(uuid4()),
    }
    return run_api_request(
        auth_file,
        client,
        REPO_AUDIT_RUNS_ENDPOINT.request(
            path_params={"repoId": request.repo_id},
            json_body=cast(EnjiJsonValue, json_request),
        ),
    )


def audit_summary_snapshot(
    repo_id: str,
    route_slug: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        REPO_AUDIT_SUMMARY_ENDPOINT.request(
            path_params={"repoId": repo_id},
            query_params={"group": route_slug},
        ),
    )


def audit_email_preferences(
    repo_id: str,
    action_key: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        AUDIT_EMAIL_PREFERENCES_GET_ENDPOINT.request(path_params={"repoId": repo_id, "actionKey": action_key}),
    )


def put_audit_email_preferences(
    repo_id: str,
    action_key: str,
    patch: JsonObjectPayload,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    request = _audit_email_preference_request(patch)
    return run_api_request(
        auth_file,
        client,
        AUDIT_EMAIL_PREFERENCES_PUT_ENDPOINT.request(
            path_params={"repoId": repo_id, "actionKey": action_key},
            json_body=cast(EnjiJsonValue, request),
        ),
    )


def audit_auto_runs(
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        AUDIT_AUTO_RUNS_ENDPOINT.request(path_params={"repoId": repo_id}),
    )


def put_audit_auto_run(
    repo_id: str,
    action_key: str,
    subscription: JsonObjectPayload,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    request = _audit_auto_run_request(subscription)
    return run_api_request(
        auth_file,
        client,
        AUDIT_AUTO_RUN_PUT_ENDPOINT.request(
            path_params={"repoId": repo_id, "actionKey": action_key},
            json_body=cast(EnjiJsonValue, request),
        ),
    )


def improvement_jobs(
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        IMPROVEMENT_JOBS_ENDPOINT.request(path_params={"repoId": repo_id}),
    )


def put_improvement_job(
    repo_id: str,
    job_kind: str,
    job: JsonObjectPayload,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return run_api_request(
        auth_file,
        client,
        IMPROVEMENT_JOB_PUT_ENDPOINT.request(
            path_params={"repoId": repo_id, "kind": job_kind},
            json_body=cast(EnjiJsonValue, job),
        ),
    )


def _reports_list_parser(selector: str) -> JsonObjectParser[ReportsListPayload]:
    def parse(payload: dict[str, object]) -> ReportsListPayload:
        return _parse_reports_list_payload(payload, selector)

    return parse


def _parse_reports_list_payload(payload: dict[str, object], selector: str) -> ReportsListPayload:
    raw_projects = payload.get("projects")
    if not isinstance(raw_projects, list):
        return {"projects": []}
    projects = [_normalize_project(project) for project in raw_projects]
    return {"projects": _filter_projects(projects, selector)}


def load_api_session(auth_file: Path | None = None) -> EnjiApiSession:
    return _load_api_session_impl(auth_file)


def _validate_reports_filters(*, stale: bool, min_severity: str | None) -> None:
    if stale:
        raise EnjiApiError("VALIDATION", "stale filtering is not available in the compact project overview yet")
    if min_severity is not None:
        raise EnjiApiError("VALIDATION", "min_severity filtering is not available in the compact project overview yet")


def _filter_projects(projects: list[ProjectOverviewPayload], selector: str) -> list[ProjectOverviewPayload]:
    if selector == "*":
        return projects
    if selector.endswith("/*"):
        project_selector = selector.removesuffix("/*")
        return [project for project in projects if _matches_project(project, project_selector)]
    if selector.startswith("repo_"):
        return [project for project in projects if selector in project["repo_ids"]]
    raise EnjiApiError("BAD_SELECTOR", "reports list currently supports '*', '<project>/*', and repo ids")


def _matches_project(project: ProjectOverviewPayload, selector: str) -> bool:
    name = project["name"]
    return project["id"] == selector or (name is not None and name.casefold() == selector.casefold())


def _normalize_project(project: object) -> ProjectOverviewPayload:
    project_object = _as_dict(project)
    return {
        "id": _optional_str(project_object.get("id")),
        "name": _optional_str(project_object.get("name")),
        "repo_ids": _normalize_str_list(project_object.get("repoIds")),
        "scores": _normalize_json_object(project_object.get("scores")),
        "recon_pending": _optional_bool(project_object.get("reconPending")),
    }


def _parse_json_object_payload(payload: dict[str, object]) -> JsonObjectPayload:
    return _normalize_json_object(payload)


def _parse_fleet_project_create_response(payload: dict[str, object]) -> FleetProjectCreateResponse:
    project_id = _optional_str(payload.get("id"))
    if project_id is None:
        raise EnjiHttpError("UPSTREAM", "project create returned no project id")
    return {"id": project_id}


ACCESS_ENDPOINT = ApiEndpoint(
    spec=ACCESS_ENDPOINT_SPEC,
    parser=_parse_access_payload,
)
USER_PREFERENCES_GET_ENDPOINT = ApiEndpoint(
    spec=USER_PREFERENCES_GET_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
USER_PREFERENCES_PUT_ENDPOINT = ApiEndpoint(
    spec=USER_PREFERENCES_PUT_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
REPORTS_LIST_ENDPOINT = ApiEndpoint(
    spec=REPORTS_LIST_ENDPOINT_SPEC,
    parser=_reports_list_parser(REPORTS_LIST_DEFAULT_SELECTOR),
)
PROJECTS_ENDPOINT = ApiEndpoint(
    spec=PROJECTS_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
PROJECT_DETAIL_ENDPOINT = ApiEndpoint(
    spec=PROJECT_DETAIL_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
PROJECT_RUN_LANGUAGE_ENDPOINT = ApiEndpoint(
    spec=PROJECT_RUN_LANGUAGE_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
FLEET_PROJECT_CREATE_ENDPOINT = ApiEndpoint(
    spec=FLEET_PROJECT_CREATE_ENDPOINT_SPEC,
    parser=_parse_fleet_project_create_response,
    expected_statuses=HTTP_CREATED_ONLY,
)
UX_PROJECT_CREATE_ENDPOINT = ApiEndpoint(
    spec=UX_PROJECT_CREATE_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
    expected_statuses=HTTP_CREATED_ONLY,
)
PROJECT_RENAME_ENDPOINT = ApiEndpoint(
    spec=PROJECT_RENAME_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
UX_PROJECT_DELETE_ENDPOINT = ApiEndpoint(
    spec=UX_PROJECT_DELETE_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
    expected_statuses=HTTP_NO_CONTENT_ONLY,
)
FLEET_PROJECT_DELETE_ENDPOINT = ApiEndpoint(
    spec=FLEET_PROJECT_DELETE_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
    expected_statuses=HTTP_NO_CONTENT_ONLY,
)
REPO_TRANSFER_PREFLIGHT_ENDPOINT = ApiEndpoint(
    spec=REPO_TRANSFER_PREFLIGHT_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
    expected_statuses=HTTP_OK_OR_NO_CONTENT,
)
REPO_TRANSFER_ENDPOINT = ApiEndpoint(
    spec=REPO_TRANSFER_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
CATALOG_ENDPOINT = ApiEndpoint(
    spec=CATALOG_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
RUNBOOK_ENDPOINT = ApiEndpoint(
    spec=RUNBOOK_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
PROJECT_REPOS_ADD_ENDPOINT = ApiEndpoint(
    spec=PROJECT_REPOS_ADD_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
    expected_statuses=HTTP_CREATED_ONLY,
)
PROJECT_REPO_DELETE_ENDPOINT = ApiEndpoint(
    spec=PROJECT_REPO_DELETE_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
    expected_statuses=HTTP_NO_CONTENT_ONLY,
)
PROJECT_REPO_CONNECTION_ENDPOINT = ApiEndpoint(
    spec=PROJECT_REPO_CONNECTION_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
REPO_ACTIVE_RUNS_ENDPOINT = ApiEndpoint(
    spec=REPO_ACTIVE_RUNS_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
REPO_AUDIT_RERUN_STATE_ENDPOINT = ApiEndpoint(
    spec=REPO_AUDIT_RERUN_STATE_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
REPO_TASK_LINKS_ENDPOINT = ApiEndpoint(
    spec=REPO_TASK_LINKS_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
REPO_AUDIT_RUNS_ENDPOINT = ApiEndpoint(
    spec=REPO_AUDIT_RUNS_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
    expected_statuses=HTTP_CREATED_ONLY,
)
REPO_AUDIT_SUMMARY_ENDPOINT = ApiEndpoint(
    spec=REPO_AUDIT_SUMMARY_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
AUDIT_EMAIL_PREFERENCES_GET_ENDPOINT = ApiEndpoint(
    spec=AUDIT_EMAIL_PREFERENCES_GET_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
AUDIT_EMAIL_PREFERENCES_PUT_ENDPOINT = ApiEndpoint(
    spec=AUDIT_EMAIL_PREFERENCES_PUT_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
AUDIT_AUTO_RUNS_ENDPOINT = ApiEndpoint(
    spec=AUDIT_AUTO_RUNS_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
AUDIT_AUTO_RUN_PUT_ENDPOINT = ApiEndpoint(
    spec=AUDIT_AUTO_RUN_PUT_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
IMPROVEMENT_JOBS_ENDPOINT = ApiEndpoint(
    spec=IMPROVEMENT_JOBS_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)
IMPROVEMENT_JOB_PUT_ENDPOINT = ApiEndpoint(
    spec=IMPROVEMENT_JOB_PUT_ENDPOINT_SPEC,
    parser=_parse_json_object_payload,
)


def _audit_email_preference_request(patch: JsonObjectPayload) -> AuditEmailPreferenceRequest:
    request: AuditEmailPreferenceRequest = {}
    manual = patch.get("manualRunCompletion")
    if manual is not None:
        if not isinstance(manual, bool):
            raise EnjiApiError("VALIDATION", "manualRunCompletion must be boolean")
        request["manualRunCompletion"] = manual
    scheduled = patch.get("scheduledRunCompletion")
    if scheduled is not None:
        if not isinstance(scheduled, bool):
            raise EnjiApiError("VALIDATION", "scheduledRunCompletion must be boolean")
        request["scheduledRunCompletion"] = scheduled
    return request


AUDIT_AUTO_RUN_FIELDS = (
    "cadence",
    "enabled",
    "scheduleDay",
    "scheduleDayOfMonth",
    "scheduleTime",
    "scheduleTimeSource",
    "timezone",
    "windowDays",
    "windowEndTime",
    "windowMode",
    "windowStartTime",
)


def _audit_auto_run_request(subscription: JsonObjectPayload) -> JsonObjectPayload:
    return {field: subscription.get(field) for field in AUDIT_AUTO_RUN_FIELDS}


def _normalize_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _normalize_json_object(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, JsonValue] = {}
    for key, item in value.items():
        if isinstance(key, str):
            normalized[key] = _normalize_json_value(item)
    return normalized


def _normalize_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        return _normalize_json_object(value)
    return None


def _normalize_json_list(value: object) -> list[JsonValue]:
    if not isinstance(value, list):
        return []
    return [_normalize_json_value(item) for item in value]


def _as_dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
