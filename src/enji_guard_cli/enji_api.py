import asyncio
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast
from urllib.parse import quote, urlencode
from uuid import uuid4

from enji_guard_cli.auth import (
    AUTH_INVALID_CODE,
    AUTH_REFRESH_ORIGIN,
    AUTH_REFRESH_PATH,
    CredentialType,
    StoredAuth,
    auth_headers,
    default_auth_file,
    is_auth_invalid_response,
    load_stored_auth,
    refresh_cookie_auth,
)
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.transport import (
    EnjiHttpClient,
    EnjiHttpError,
    EnjiHttpRequest,
    EnjiHttpResponse,
    EnjiJsonValue,
    HttpxEnjiHttpClient,
    raise_for_response_status,
)

HTTP_OK = 200
HTTP_CREATED = 201
HTTP_NO_CONTENT = 204
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
REPORTS_LIST_DEFAULT_SELECTOR = "*"
REPORTS_LIST_DEFAULT_STALE = False
REPORTS_LIST_DEFAULT_MIN_SEVERITY: str | None = None
HTTP_OK_ONLY = frozenset({HTTP_OK})
HTTP_CREATED_ONLY = frozenset({HTTP_CREATED})
HTTP_NO_CONTENT_ONLY = frozenset({HTTP_NO_CONTENT})
HTTP_OK_OR_NO_CONTENT = frozenset({HTTP_OK, HTTP_NO_CONTENT})

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObjectParser[T] = Callable[[dict[str, object]], T]
type JsonObjectPayload = dict[str, JsonValue]
type HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
type ApiPathParams = Mapping[str, str]
type ApiQueryParams = Mapping[str, str]


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


class ApiEndpointPayload(TypedDict):
    method: HttpMethod
    path_template: str
    operation_id: str
    operation: str
    request_body_ref: str | None


@dataclass(slots=True)
class EnjiApiSession:
    auth_file: Path
    base_url: str
    headers: dict[str, str]
    stored_auth: StoredAuth
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refresh_epoch: int = 0

    def update_stored_auth(self, stored_auth: StoredAuth) -> None:
        self.stored_auth = stored_auth
        self.headers = api_headers(stored_auth)
        self.refresh_epoch += 1


@dataclass(frozen=True, slots=True)
class ApiRequestSpec[T]:
    method: HttpMethod
    path: str
    operation: str
    parser: JsonObjectParser[T]
    json_body: EnjiJsonValue | None = None
    expected_statuses: Collection[int] = HTTP_OK_ONLY


@dataclass(frozen=True, slots=True)
class ApiEndpoint[T]:
    method: HttpMethod
    path_template: str
    operation_id: str
    operation: str
    parser: JsonObjectParser[T]
    request_body_ref: str | None = None
    expected_statuses: Collection[int] = HTTP_OK_ONLY

    def request(
        self,
        *,
        path_params: ApiPathParams | None = None,
        query_params: ApiQueryParams | None = None,
        json_body: EnjiJsonValue | None = None,
        parser: JsonObjectParser[T] | None = None,
    ) -> ApiRequestSpec[T]:
        path = _render_api_path(self.path_template, path_params)
        if query_params:
            path = f"{path}?{urlencode(query_params)}"
        return ApiRequestSpec(
            method=self.method,
            path=path,
            operation=self.operation,
            parser=parser if parser is not None else self.parser,
            json_body=json_body,
            expected_statuses=self.expected_statuses,
        )

    def catalog_entry(self) -> ApiEndpointPayload:
        return {
            "method": self.method,
            "path_template": self.path_template,
            "operation_id": self.operation_id,
            "operation": self.operation,
            "request_body_ref": self.request_body_ref,
        }


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


class RepoTransferPreflightRequest(TypedDict):
    targetProjectId: str


class RepoTransferRequest(TypedDict):
    targetProjectId: str
    scheduleReplacements: NotRequired[dict[str, JsonValue]]


class RepoConnectRequest(TypedDict):
    githubOwner: str
    githubName: str


class RepoConnectionRequest(TypedDict):
    connected: bool


class AuditRunCreateRequest(TypedDict):
    projectId: str
    actionKey: str
    fleetTaskBody: JsonObjectPayload
    clientRequestId: str


class AuditEmailPreferenceRequest(TypedDict, total=False):
    manualRunCompletion: bool
    scheduledRunCompletion: bool


def access(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AccessPayload:
    return _run_api_request(
        auth_file,
        client,
        ACCESS_ENDPOINT.request(),
    )


async def access_async(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AccessPayload:
    return await _run_api_request_async(
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
    return _run_api_request(
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
    return await _run_api_request_async(
        auth_file,
        client,
        REPORTS_LIST_ENDPOINT.request(parser=_reports_list_parser(selector)),
    )


def projects(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> JsonObjectPayload:
    return _run_api_request(auth_file, client, PROJECTS_ENDPOINT.request())


async def projects_async(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> JsonObjectPayload:
    return await _run_api_request_async(auth_file, client, PROJECTS_ENDPOINT.request())


def project_detail(
    project_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return _run_api_request(
        auth_file,
        client,
        PROJECT_DETAIL_ENDPOINT.request(path_params={"projectId": project_id}),
    )


def create_project(
    name: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    fleet_request: FleetProjectCreateRequest = {"name": name}
    fleet_project = _run_api_request(
        auth_file,
        client,
        FLEET_PROJECT_CREATE_ENDPOINT.request(json_body=cast(EnjiJsonValue, fleet_request)),
    )
    ux_request: UxProjectCreateRequest = {
        "fleetProjectId": fleet_project["id"],
        "name": name,
        "createdAt": datetime.now(UTC).isoformat(),
    }
    return _run_api_request(
        auth_file,
        client,
        UX_PROJECT_CREATE_ENDPOINT.request(json_body=cast(EnjiJsonValue, ux_request)),
    )


def rename_project(
    project_id: str,
    name: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    patch: ProjectPatchRequest = {"name": name}
    return _run_api_request(
        auth_file,
        client,
        PROJECT_RENAME_ENDPOINT.request(path_params={"projectId": project_id}, json_body=cast(EnjiJsonValue, patch)),
    )


def delete_project(
    project_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> None:
    _run_api_no_content(
        auth_file,
        client,
        UX_PROJECT_DELETE_ENDPOINT.request(path_params={"projectId": project_id}),
    )
    _run_api_no_content(
        auth_file,
        client,
        FLEET_PROJECT_DELETE_ENDPOINT.request(path_params={"projectId": project_id}),
    )


def preflight_repo_move(
    source_project_id: str,
    repo_id: str,
    target_project_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    request: RepoTransferPreflightRequest = {"targetProjectId": target_project_id}
    return _run_api_no_content(
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
    return _run_api_request(
        auth_file,
        client,
        REPO_TRANSFER_ENDPOINT.request(
            path_params={"sourceProjectId": request.source_project_id, "repoId": request.repo_id},
            json_body=cast(EnjiJsonValue, json_request),
        ),
    )


def catalog(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> JsonObjectPayload:
    return _run_api_request(auth_file, client, CATALOG_ENDPOINT.request())


def runbook(
    runbook_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return _run_api_request(
        auth_file,
        client,
        RUNBOOK_ENDPOINT.request(path_params={"runbookId": runbook_id}),
    )


def github_installations(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> JsonObjectPayload:
    return _run_api_request(
        auth_file,
        client,
        GITHUB_INSTALLATIONS_ENDPOINT.request(),
    )


def github_installation_repos(
    installation_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return _run_api_request(
        auth_file,
        client,
        GITHUB_INSTALLATION_REPOS_ENDPOINT.request(path_params={"installationId": installation_id}),
    )


def connect_project_repo(
    project_id: str,
    github_owner: str,
    github_name: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    request: RepoConnectRequest = {"githubOwner": github_owner, "githubName": github_name}
    return _run_api_request(
        auth_file,
        client,
        PROJECT_REPOS_CONNECT_ENDPOINT.request(
            path_params={"projectId": project_id},
            json_body=cast(EnjiJsonValue, request),
        ),
    )


def update_repo_connection(
    project_id: str,
    repo_id: str,
    *,
    connected: bool,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    request: RepoConnectionRequest = {"connected": connected}
    return _run_api_request(
        auth_file,
        client,
        PROJECT_REPO_CONNECTION_ENDPOINT.request(
            path_params={"projectId": project_id, "repoId": repo_id},
            json_body=cast(EnjiJsonValue, request),
        ),
    )


def project_active_runs(
    project_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return _run_api_request(
        auth_file,
        client,
        PROJECT_ACTIVE_RUNS_ENDPOINT.request(path_params={"projectId": project_id}),
    )


def repo_active_runs(
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return _run_api_request(
        auth_file,
        client,
        REPO_ACTIVE_RUNS_ENDPOINT.request(path_params={"repoId": repo_id}),
    )


def repo_audit_rerun_state(
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return _run_api_request(
        auth_file,
        client,
        REPO_AUDIT_RERUN_STATE_ENDPOINT.request(path_params={"repoId": repo_id}),
    )


def repo_task_links(
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return _run_api_request(
        auth_file,
        client,
        REPO_TASK_LINKS_ENDPOINT.request(path_params={"repoId": repo_id}),
    )


def repo_audit_history(
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return _run_api_request(
        auth_file,
        client,
        REPO_AUDIT_HISTORY_ENDPOINT.request(path_params={"repoId": repo_id}),
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
    return _run_api_request(
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
    return _run_api_request(
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
    return _run_api_request(
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
    return _run_api_request(
        auth_file,
        client,
        AUDIT_EMAIL_PREFERENCES_PUT_ENDPOINT.request(
            path_params={"repoId": repo_id, "actionKey": action_key},
            json_body=cast(EnjiJsonValue, request),
        ),
    )


def improvement_jobs(
    repo_id: str,
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> JsonObjectPayload:
    return _run_api_request(
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
    return _run_api_request(
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
    target = auth_file if auth_file is not None else default_auth_file()
    if not target.exists():
        raise EnjiApiError("AUTH_REQUIRED", "auth file does not exist")

    stored_auth = load_stored_auth(target)
    if stored_auth is None:
        raise EnjiApiError("AUTH_REQUIRED", "auth file is invalid")

    return EnjiApiSession(
        auth_file=target, base_url=stored_auth["base_url"], headers=api_headers(stored_auth), stored_auth=stored_auth
    )


def api_headers(stored_auth: StoredAuth) -> dict[str, str]:
    return {**auth_headers(stored_auth), "Origin": AUTH_REFRESH_ORIGIN}


def _run_api_request[T](
    auth_file: Path | None,
    client: EnjiHttpClient | None,
    spec: ApiRequestSpec[T],
) -> T:
    return asyncio.run(
        _run_api_request_async(
            auth_file,
            client,
            spec,
        )
    )


async def _run_api_request_async[T](
    auth_file: Path | None,
    client: EnjiHttpClient | None,
    spec: ApiRequestSpec[T],
) -> T:
    try:
        session = load_api_session(auth_file)
        if client is not None:
            return await _request_parsed_json_object(session, client, spec)

        async with HttpxEnjiHttpClient() as owned_client:
            return await _request_parsed_json_object(session, owned_client, spec)
    except EnjiHttpError as exc:
        raise EnjiApiError(exc.code, exc.message) from exc


def _run_api_no_content(
    auth_file: Path | None,
    client: EnjiHttpClient | None,
    spec: ApiRequestSpec[JsonObjectPayload],
) -> JsonObjectPayload:
    return asyncio.run(_run_api_no_content_async(auth_file, client, spec))


async def _run_api_no_content_async(
    auth_file: Path | None,
    client: EnjiHttpClient | None,
    spec: ApiRequestSpec[JsonObjectPayload],
) -> JsonObjectPayload:
    try:
        session = load_api_session(auth_file)
        if client is not None:
            return await _request_no_content(session, client, spec)

        async with HttpxEnjiHttpClient() as owned_client:
            return await _request_no_content(session, owned_client, spec)
    except EnjiHttpError as exc:
        raise EnjiApiError(exc.code, exc.message) from exc


async def _request_parsed_json_object[T](
    session: EnjiApiSession,
    client: EnjiHttpClient,
    spec: ApiRequestSpec[T],
) -> T:
    return spec.parser(await _request_json_object(session, client, spec))


async def _request_no_content(
    session: EnjiApiSession,
    client: EnjiHttpClient,
    spec: ApiRequestSpec[JsonObjectPayload],
) -> JsonObjectPayload:
    response = await _request_with_refresh(
        session,
        client,
        EnjiHttpRequest(
            method=spec.method,
            url=f"{session.base_url}{spec.path}",
            operation=spec.operation,
            headers=dict(session.headers),
            json_body=spec.json_body,
        ),
    )
    _raise_for_api_response_status(response, operation=spec.operation, expected_statuses=spec.expected_statuses)
    if not response.content:
        return {}
    payload = response.json(operation=spec.operation)
    if not isinstance(payload, dict):
        raise EnjiHttpError("UPSTREAM", f"{spec.operation} returned unexpected JSON")
    return _normalize_json_object(payload)


async def _get_json_object(
    session: EnjiApiSession,
    client: EnjiHttpClient,
    *,
    path: str,
    operation: str,
) -> dict[str, object]:
    return await _request_json_object(
        session,
        client,
        ApiRequestSpec(method="GET", path=path, operation=operation, parser=_parse_json_object_payload),
    )


async def _request_json_object[T](
    session: EnjiApiSession,
    client: EnjiHttpClient,
    spec: ApiRequestSpec[T],
) -> dict[str, object]:
    response = await _request_with_refresh(
        session,
        client,
        EnjiHttpRequest(
            method=spec.method,
            url=f"{session.base_url}{spec.path}",
            operation=spec.operation,
            headers=dict(session.headers),
            json_body=spec.json_body,
        ),
    )
    _raise_for_api_response_status(response, operation=spec.operation, expected_statuses=spec.expected_statuses)
    payload = response.json(operation=spec.operation)
    if not isinstance(payload, dict):
        raise EnjiHttpError("UPSTREAM", f"{spec.operation} returned unexpected JSON")
    return cast(dict[str, object], payload)


async def _request_with_refresh(
    session: EnjiApiSession,
    client: EnjiHttpClient,
    request: EnjiHttpRequest,
) -> EnjiHttpResponse:
    refresh_epoch = session.refresh_epoch
    response = await client.request(request)
    if not _should_refresh(session, request, response):
        return response

    await _refresh_session_once(session, client, refresh_epoch)
    retry_response = await client.request(_request_with_current_headers(request, session))
    if is_auth_invalid_response(retry_response):
        raise EnjiHttpError(AUTH_INVALID_CODE, "invalid access token after refresh", status_code=HTTP_UNAUTHORIZED)
    return retry_response


def _should_refresh(session: EnjiApiSession, request: EnjiHttpRequest, response: EnjiHttpResponse) -> bool:
    if not _is_cookie_session(session):
        return False
    if request.url == f"{session.base_url}{AUTH_REFRESH_PATH}":
        return False
    return response.status_code in {HTTP_UNAUTHORIZED, HTTP_FORBIDDEN} or is_auth_invalid_response(response)


async def _refresh_session_once(
    session: EnjiApiSession,
    client: EnjiHttpClient,
    observed_refresh_epoch: int,
) -> None:
    async with session.refresh_lock:
        if session.refresh_epoch != observed_refresh_epoch:
            return
        await _refresh_session(session, client)


async def _refresh_session(session: EnjiApiSession, client: EnjiHttpClient) -> None:
    session.update_stored_auth(await refresh_cookie_auth(session.auth_file, session.stored_auth, client))


def _request_with_current_headers(request: EnjiHttpRequest, session: EnjiApiSession) -> EnjiHttpRequest:
    return EnjiHttpRequest(
        method=request.method,
        url=request.url,
        operation=request.operation,
        headers=dict(session.headers),
        json_body=request.json_body,
        timeout_seconds=request.timeout_seconds,
    )


def _raise_for_api_response_status(
    response: EnjiHttpResponse,
    *,
    operation: str,
    expected_statuses: Collection[int],
) -> None:
    if response.status_code in expected_statuses:
        return
    if is_auth_invalid_response(response):
        raise EnjiHttpError(AUTH_INVALID_CODE, "invalid access token", status_code=HTTP_UNAUTHORIZED)
    if response.status_code == HTTP_UNAUTHORIZED:
        raise EnjiHttpError("AUTH_REQUIRED", "stored credential is not authenticated", status_code=response.status_code)
    api_error = _api_error_from_response(response)
    if api_error is not None:
        raise api_error
    raise_for_response_status(response, operation=operation, expected_statuses=expected_statuses)


def _api_error_from_response(response: EnjiHttpResponse) -> EnjiHttpError | None:
    try:
        payload = response.json(operation="api error")
    except EnjiHttpError:
        return None
    error = _as_dict(payload)
    nested_error = _as_dict(error.get("error"))
    error_payload = nested_error or error
    code = _optional_str(error_payload.get("code"))
    if code is None:
        return None
    message = _optional_str(error_payload.get("message")) or code
    return EnjiHttpError(code, message, status_code=response.status_code)


def _is_cookie_session(session: EnjiApiSession) -> bool:
    return session.stored_auth["credential"]["type"] == CredentialType.COOKIE.value


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
    return project["id"] == selector or project["name"] == selector


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
    method="GET",
    path_template="/api/ux/me/access",
    operation_id="getUxMeAccess",
    operation="access",
    parser=_parse_access_payload,
)
REPORTS_LIST_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/projects",
    operation_id="listProjects",
    operation="reports list",
    parser=_reports_list_parser(REPORTS_LIST_DEFAULT_SELECTOR),
)
PROJECTS_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/projects",
    operation_id="listProjects",
    operation="repo list",
    parser=_parse_json_object_payload,
)
PROJECT_DETAIL_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/projects/{projectId}",
    operation_id="getProject",
    operation="project detail",
    parser=_parse_json_object_payload,
)
FLEET_PROJECT_CREATE_ENDPOINT = ApiEndpoint(
    method="POST",
    path_template="/api/v1/projects",
    operation_id="createFleetProject",
    operation="project create",
    parser=_parse_fleet_project_create_response,
    request_body_ref="#/components/requestBodies/FleetProjectCreate",
    expected_statuses=HTTP_CREATED_ONLY,
)
UX_PROJECT_CREATE_ENDPOINT = ApiEndpoint(
    method="POST",
    path_template="/api/ux/projects",
    operation_id="createUxProject",
    operation="project create",
    parser=_parse_json_object_payload,
    request_body_ref="#/components/requestBodies/UxProjectCreate",
    expected_statuses=HTTP_CREATED_ONLY,
)
PROJECT_RENAME_ENDPOINT = ApiEndpoint(
    method="PATCH",
    path_template="/api/ux/projects/{projectId}",
    operation_id="patchProject",
    operation="project rename",
    parser=_parse_json_object_payload,
    request_body_ref="#/components/requestBodies/ProjectPatch",
)
UX_PROJECT_DELETE_ENDPOINT = ApiEndpoint(
    method="DELETE",
    path_template="/api/ux/projects/{projectId}",
    operation_id="deleteUxProject",
    operation="project delete",
    parser=_parse_json_object_payload,
    expected_statuses=HTTP_NO_CONTENT_ONLY,
)
FLEET_PROJECT_DELETE_ENDPOINT = ApiEndpoint(
    method="DELETE",
    path_template="/api/v1/projects/{projectId}",
    operation_id="deleteFleetProject",
    operation="project delete",
    parser=_parse_json_object_payload,
    expected_statuses=HTTP_NO_CONTENT_ONLY,
)
REPO_TRANSFER_PREFLIGHT_ENDPOINT = ApiEndpoint(
    method="POST",
    path_template="/api/ux/projects/{sourceProjectId}/repos/{repoId}/transfer/preflight",
    operation_id="preflightRepoTransfer",
    operation="repo move preflight",
    parser=_parse_json_object_payload,
    request_body_ref="#/components/requestBodies/RepoTransferPreflight",
    expected_statuses=HTTP_OK_OR_NO_CONTENT,
)
REPO_TRANSFER_ENDPOINT = ApiEndpoint(
    method="POST",
    path_template="/api/ux/projects/{sourceProjectId}/repos/{repoId}/transfer",
    operation_id="transferRepo",
    operation="repo move",
    parser=_parse_json_object_payload,
    request_body_ref="#/components/requestBodies/RepoTransfer",
)
CATALOG_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/catalog",
    operation_id="getUxCatalog",
    operation="catalog",
    parser=_parse_json_object_payload,
)
RUNBOOK_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/v1/runbooks/{runbookId}",
    operation_id="getRunbook",
    operation="runbook",
    parser=_parse_json_object_payload,
)
GITHUB_INSTALLATIONS_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/github-installations",
    operation_id="listGitHubInstallations",
    operation="repo github installations",
    parser=_parse_json_object_payload,
)
GITHUB_INSTALLATION_REPOS_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/v1/github/app/installations/{installationId}/repos",
    operation_id="listGitHubInstallationRepos",
    operation="repo github repos",
    parser=_parse_json_object_payload,
)
PROJECT_REPOS_CONNECT_ENDPOINT = ApiEndpoint(
    method="POST",
    path_template="/api/ux/projects/{projectId}/repos",
    operation_id="connectProjectRepo",
    operation="repo add",
    parser=_parse_json_object_payload,
    request_body_ref="#/components/requestBodies/GitHubRepoConnect",
    expected_statuses=HTTP_CREATED_ONLY,
)
PROJECT_REPO_CONNECTION_ENDPOINT = ApiEndpoint(
    method="PUT",
    path_template="/api/ux/projects/{projectId}/repos/{repoId}/connection",
    operation_id="putProjectRepoConnection",
    operation="repo connection",
    parser=_parse_json_object_payload,
    request_body_ref="#/components/requestBodies/RepoConnectionUpdate",
)
PROJECT_ACTIVE_RUNS_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/projects/{projectId}/active-runs",
    operation_id="listProjectActiveRuns",
    operation="project active runs",
    parser=_parse_json_object_payload,
)
REPO_ACTIVE_RUNS_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/repos/{repoId}/active-runs",
    operation_id="listRepoActiveRuns",
    operation="repo active runs",
    parser=_parse_json_object_payload,
)
REPO_AUDIT_RERUN_STATE_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/repos/{repoId}/audit-rerun-state",
    operation_id="getRepoAuditRerunState",
    operation="repo rerun state",
    parser=_parse_json_object_payload,
)
REPO_TASK_LINKS_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/repos/{repoId}/task-links",
    operation_id="listRepoTaskLinks",
    operation="repo task links",
    parser=_parse_json_object_payload,
)
REPO_AUDIT_HISTORY_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/repos/{repoId}/audit-history",
    operation_id="listRepoAuditHistory",
    operation="repo audit history",
    parser=_parse_json_object_payload,
)
REPO_AUDIT_RUNS_ENDPOINT = ApiEndpoint(
    method="POST",
    path_template="/api/ux/repos/{repoId}/audit-runs",
    operation_id="createRepoAuditRun",
    operation="audit start",
    parser=_parse_json_object_payload,
    request_body_ref="#/components/requestBodies/AuditRunCreate",
    expected_statuses=HTTP_CREATED_ONLY,
)
REPO_AUDIT_SUMMARY_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/repos/{repoId}/snapshots/upfront.audit.summary",
    operation_id="getRepoAuditSummarySnapshot",
    operation="report show",
    parser=_parse_json_object_payload,
)
AUDIT_EMAIL_PREFERENCES_GET_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/repos/{repoId}/audits/{actionKey}/email-preferences",
    operation_id="getAuditEmailPreferences",
    operation="email list",
    parser=_parse_json_object_payload,
)
AUDIT_EMAIL_PREFERENCES_PUT_ENDPOINT = ApiEndpoint(
    method="PUT",
    path_template="/api/ux/repos/{repoId}/audits/{actionKey}/email-preferences",
    operation_id="putAuditEmailPreferences",
    operation="email set",
    parser=_parse_json_object_payload,
    request_body_ref="#/components/requestBodies/EmailPreferencesPatch",
)
IMPROVEMENT_JOBS_ENDPOINT = ApiEndpoint(
    method="GET",
    path_template="/api/ux/improvement-jobs/{repoId}",
    operation_id="listImprovementJobs",
    operation="schedule list",
    parser=_parse_json_object_payload,
)
IMPROVEMENT_JOB_PUT_ENDPOINT = ApiEndpoint(
    method="PUT",
    path_template="/api/ux/improvement-jobs/{repoId}/{kind}",
    operation_id="putImprovementJob",
    operation="schedule set",
    parser=_parse_json_object_payload,
    request_body_ref="#/components/schemas/ImprovementJobUpdate",
)


def implemented_api_endpoints() -> list[ApiEndpointPayload]:
    return [
        endpoint.catalog_entry()
        for endpoint in (
            ACCESS_ENDPOINT,
            REPORTS_LIST_ENDPOINT,
            PROJECTS_ENDPOINT,
            PROJECT_DETAIL_ENDPOINT,
            FLEET_PROJECT_CREATE_ENDPOINT,
            UX_PROJECT_CREATE_ENDPOINT,
            PROJECT_RENAME_ENDPOINT,
            UX_PROJECT_DELETE_ENDPOINT,
            FLEET_PROJECT_DELETE_ENDPOINT,
            REPO_TRANSFER_PREFLIGHT_ENDPOINT,
            REPO_TRANSFER_ENDPOINT,
            CATALOG_ENDPOINT,
            RUNBOOK_ENDPOINT,
            GITHUB_INSTALLATIONS_ENDPOINT,
            GITHUB_INSTALLATION_REPOS_ENDPOINT,
            PROJECT_REPOS_CONNECT_ENDPOINT,
            PROJECT_REPO_CONNECTION_ENDPOINT,
            PROJECT_ACTIVE_RUNS_ENDPOINT,
            REPO_ACTIVE_RUNS_ENDPOINT,
            REPO_AUDIT_RERUN_STATE_ENDPOINT,
            REPO_TASK_LINKS_ENDPOINT,
            REPO_AUDIT_HISTORY_ENDPOINT,
            REPO_AUDIT_RUNS_ENDPOINT,
            REPO_AUDIT_SUMMARY_ENDPOINT,
            AUDIT_EMAIL_PREFERENCES_GET_ENDPOINT,
            AUDIT_EMAIL_PREFERENCES_PUT_ENDPOINT,
            IMPROVEMENT_JOBS_ENDPOINT,
            IMPROVEMENT_JOB_PUT_ENDPOINT,
        )
    ]


def _render_api_path(path_template: str, path_params: ApiPathParams | None) -> str:
    path = path_template
    for name, value in (path_params or {}).items():
        path = path.replace(f"{{{name}}}", _quote_path(value))
    if "{" in path or "}" in path:
        raise ValueError(f"unresolved API path parameter in {path_template}")
    return path


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


def _quote_path(value: str) -> str:
    return quote(value, safe="")
