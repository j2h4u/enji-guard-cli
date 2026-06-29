import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, cast

from enji_guard_cli.auth import (
    AUTH_INVALID_CODE,
    AUTH_REFRESH_PATH,
    CredentialType,
    StoredAuth,
    auth_headers,
    default_auth_file,
    is_auth_invalid_response,
    load_stored_auth,
    refresh_cookie_auth,
)
from enji_guard_cli.transport import (
    EnjiHttpClient,
    EnjiHttpError,
    EnjiHttpRequest,
    EnjiHttpResponse,
    HttpxEnjiHttpClient,
    raise_for_response_status,
)

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
REPORTS_LIST_DEFAULT_SELECTOR = "*"
REPORTS_LIST_DEFAULT_STALE = False
REPORTS_LIST_DEFAULT_MIN_SEVERITY: str | None = None

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObjectParser[T] = Callable[[dict[str, object]], T]


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


class EnjiApiError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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
        self.headers = auth_headers(stored_auth)
        self.refresh_epoch += 1


def access(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AccessPayload:
    return _run_api_get(
        auth_file,
        client,
        path="/api/ux/me/access",
        operation="access",
        parser=_parse_access_payload,
    )


async def access_async(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AccessPayload:
    return await _run_api_get_async(
        auth_file,
        client,
        path="/api/ux/me/access",
        operation="access",
        parser=_parse_access_payload,
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
    return _run_api_get(
        auth_file,
        client,
        path="/api/ux/projects",
        operation="reports list",
        parser=_reports_list_parser(selector),
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
    return await _run_api_get_async(
        auth_file,
        client,
        path="/api/ux/projects",
        operation="reports list",
        parser=_reports_list_parser(selector),
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
        auth_file=target, base_url=stored_auth["base_url"], headers=auth_headers(stored_auth), stored_auth=stored_auth
    )


def _run_api_get[T](
    auth_file: Path | None,
    client: EnjiHttpClient | None,
    *,
    path: str,
    operation: str,
    parser: JsonObjectParser[T],
) -> T:
    return asyncio.run(
        _run_api_get_async(
            auth_file,
            client,
            path=path,
            operation=operation,
            parser=parser,
        )
    )


async def _run_api_get_async[T](
    auth_file: Path | None,
    client: EnjiHttpClient | None,
    *,
    path: str,
    operation: str,
    parser: JsonObjectParser[T],
) -> T:
    try:
        session = load_api_session(auth_file)
        if client is not None:
            return await _get_parsed_json_object(session, client, path=path, operation=operation, parser=parser)

        async with HttpxEnjiHttpClient() as owned_client:
            return await _get_parsed_json_object(session, owned_client, path=path, operation=operation, parser=parser)
    except EnjiHttpError as exc:
        raise EnjiApiError(exc.code, exc.message) from exc


async def _get_parsed_json_object[T](
    session: EnjiApiSession,
    client: EnjiHttpClient,
    *,
    path: str,
    operation: str,
    parser: JsonObjectParser[T],
) -> T:
    return parser(await _get_json_object(session, client, path=path, operation=operation))


async def _get_json_object(
    session: EnjiApiSession,
    client: EnjiHttpClient,
    *,
    path: str,
    operation: str,
) -> dict[str, object]:
    response = await _request_with_refresh(
        session,
        client,
        EnjiHttpRequest(
            method="GET",
            url=f"{session.base_url}{path}",
            operation=operation,
            headers=dict(session.headers),
        ),
    )
    _raise_for_api_response_status(response, operation=operation)
    payload = response.json(operation=operation)
    if not isinstance(payload, dict):
        raise EnjiHttpError("UPSTREAM", f"{operation} returned unexpected JSON")
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
    return is_auth_invalid_response(response)


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
        timeout_seconds=request.timeout_seconds,
    )


def _raise_for_api_response_status(response: EnjiHttpResponse, *, operation: str) -> None:
    if response.status_code == HTTP_OK:
        return
    if is_auth_invalid_response(response):
        raise EnjiHttpError(AUTH_INVALID_CODE, "invalid access token", status_code=HTTP_UNAUTHORIZED)
    if response.status_code == HTTP_UNAUTHORIZED:
        raise EnjiHttpError("AUTH_REQUIRED", "stored credential is not authenticated", status_code=response.status_code)
    raise_for_response_status(response, operation=operation, expected_statuses={HTTP_OK})


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
