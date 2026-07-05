import asyncio
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlencode

from enji_guard_cli._enji_api_contract import EnjiEndpointSpec, HttpMethod
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
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue
from enji_guard_cli.settings import default_settings
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
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_OK_ONLY = frozenset({HTTP_OK})
AUTH_REQUIRED_CODE = "AUTH_REQUIRED"
REFRESHABLE_FORBIDDEN_CODES = frozenset({AUTH_INVALID_CODE, AUTH_REQUIRED_CODE})

type JsonObjectParser[T] = Callable[[dict[str, object]], T]
type ApiPathParams = Mapping[str, str]
type ApiQueryParams = Mapping[str, str]


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
    spec: EnjiEndpointSpec
    parser: JsonObjectParser[T]
    expected_statuses: Collection[int] = HTTP_OK_ONLY

    def request(
        self,
        *,
        path_params: ApiPathParams | None = None,
        query_params: ApiQueryParams | None = None,
        json_body: EnjiJsonValue | None = None,
        parser: JsonObjectParser[T] | None = None,
    ) -> ApiRequestSpec[T]:
        path = _render_api_path(self.spec.path_template, path_params)
        if query_params:
            path = f"{path}?{urlencode(query_params)}"
        return ApiRequestSpec(
            method=self.spec.method,
            path=path,
            operation=self.spec.operation,
            parser=parser if parser is not None else self.parser,
            json_body=json_body,
            expected_statuses=self.expected_statuses,
        )


def load_api_session(auth_file: Path | None = None) -> EnjiApiSession:
    target = auth_file if auth_file is not None else default_auth_file()
    if not target.exists():
        raise EnjiApiError("AUTH_REQUIRED", "auth file does not exist")

    stored_auth = load_stored_auth(target)
    if stored_auth is None:
        raise EnjiApiError("AUTH_REQUIRED", "auth file is invalid")

    return EnjiApiSession(
        auth_file=target,
        base_url=stored_auth["base_url"],
        headers=api_headers(stored_auth),
        stored_auth=stored_auth,
    )


def api_headers(stored_auth: StoredAuth) -> dict[str, str]:
    return {**auth_headers(stored_auth), "Origin": default_settings().auth.guard_origin}


def run_api_request[T](
    auth_file: Path | None,
    client: EnjiHttpClient | None,
    spec: ApiRequestSpec[T],
) -> T:
    return asyncio.run(run_api_request_async(auth_file, client, spec))


async def run_api_request_async[T](
    auth_file: Path | None,
    client: EnjiHttpClient | None,
    spec: ApiRequestSpec[T],
) -> T:
    try:
        session = load_api_session(auth_file)
        if client is not None:
            return await request_parsed_json_object(session, client, spec)

        async with HttpxEnjiHttpClient() as owned_client:
            return await request_parsed_json_object(session, owned_client, spec)
    except EnjiHttpError as exc:
        raise EnjiApiError(exc.code, exc.message) from exc


def run_api_no_content(
    auth_file: Path | None,
    client: EnjiHttpClient | None,
    spec: ApiRequestSpec[JsonObjectPayload],
) -> JsonObjectPayload:
    return asyncio.run(run_api_no_content_async(auth_file, client, spec))


async def run_api_no_content_async(
    auth_file: Path | None,
    client: EnjiHttpClient | None,
    spec: ApiRequestSpec[JsonObjectPayload],
) -> JsonObjectPayload:
    try:
        session = load_api_session(auth_file)
        if client is not None:
            return await request_no_content(session, client, spec)

        async with HttpxEnjiHttpClient() as owned_client:
            return await request_no_content(session, owned_client, spec)
    except EnjiHttpError as exc:
        raise EnjiApiError(exc.code, exc.message) from exc


async def request_parsed_json_object[T](
    session: EnjiApiSession,
    client: EnjiHttpClient,
    spec: ApiRequestSpec[T],
) -> T:
    return spec.parser(await request_json_object(session, client, spec))


async def request_no_content(
    session: EnjiApiSession,
    client: EnjiHttpClient,
    spec: ApiRequestSpec[JsonObjectPayload],
) -> JsonObjectPayload:
    response = await request_with_refresh(
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
    raise_for_api_response_status(response, operation=spec.operation, expected_statuses=spec.expected_statuses)
    if not response.content:
        return {}
    payload = response.json(operation=spec.operation)
    if not isinstance(payload, dict):
        raise EnjiHttpError("UPSTREAM", f"{spec.operation} returned unexpected JSON")
    return normalize_json_object(payload)


async def get_json_object(
    session: EnjiApiSession,
    client: EnjiHttpClient,
    *,
    path: str,
    operation: str,
) -> dict[str, object]:
    return await request_json_object(
        session,
        client,
        ApiRequestSpec(method="GET", path=path, operation=operation, parser=normalize_json_object),
    )


async def request_json_object[T](
    session: EnjiApiSession,
    client: EnjiHttpClient,
    spec: ApiRequestSpec[T],
) -> dict[str, object]:
    response = await request_with_refresh(
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
    raise_for_api_response_status(response, operation=spec.operation, expected_statuses=spec.expected_statuses)
    payload = response.json(operation=spec.operation)
    if not isinstance(payload, dict):
        raise EnjiHttpError("UPSTREAM", f"{spec.operation} returned unexpected JSON")
    return cast(dict[str, object], payload)


async def request_with_refresh(
    session: EnjiApiSession,
    client: EnjiHttpClient,
    request: EnjiHttpRequest,
) -> EnjiHttpResponse:
    refresh_epoch = session.refresh_epoch
    response = await client.request(request)
    if not should_refresh(session, request, response):
        return response

    await refresh_session_once(session, client, refresh_epoch)
    retry_response = await client.request(request_with_current_headers(request, session))
    if is_auth_invalid_response(retry_response):
        raise EnjiHttpError(AUTH_INVALID_CODE, "invalid access token after refresh", status_code=HTTP_UNAUTHORIZED)
    return retry_response


def should_refresh(session: EnjiApiSession, request: EnjiHttpRequest, response: EnjiHttpResponse) -> bool:
    if not is_cookie_session(session):
        return False
    if request.url == f"{session.base_url}{AUTH_REFRESH_PATH}":
        return False
    return response.status_code == HTTP_UNAUTHORIZED or is_refreshable_forbidden_response(response)


def is_refreshable_forbidden_response(response: EnjiHttpResponse) -> bool:
    if response.status_code != HTTP_FORBIDDEN:
        return False
    api_error = api_error_from_response(response)
    return api_error is not None and api_error.code in REFRESHABLE_FORBIDDEN_CODES


async def refresh_session_once(
    session: EnjiApiSession,
    client: EnjiHttpClient,
    observed_refresh_epoch: int,
) -> None:
    async with session.refresh_lock:
        if session.refresh_epoch != observed_refresh_epoch:
            return
        await refresh_session(session, client)


async def refresh_session(session: EnjiApiSession, client: EnjiHttpClient) -> None:
    session.update_stored_auth(await refresh_cookie_auth(session.auth_file, session.stored_auth, client))


def request_with_current_headers(request: EnjiHttpRequest, session: EnjiApiSession) -> EnjiHttpRequest:
    return EnjiHttpRequest(
        method=request.method,
        url=request.url,
        operation=request.operation,
        headers=dict(session.headers),
        json_body=request.json_body,
        timeout_seconds=request.timeout_seconds,
    )


def raise_for_api_response_status(
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
    api_error = api_error_from_response(response)
    if api_error is not None:
        raise api_error
    raise_for_response_status(response, operation=operation, expected_statuses=expected_statuses)


def api_error_from_response(response: EnjiHttpResponse) -> EnjiHttpError | None:
    try:
        payload = response.json(operation="api error")
    except EnjiHttpError:
        return None
    error = as_dict(payload)
    nested_error = as_dict(error.get("error"))
    error_payload = nested_error or error
    code = optional_str(error_payload.get("code"))
    if code is None:
        return None
    message = optional_str(error_payload.get("message")) or code
    return EnjiHttpError(code, message, status_code=response.status_code)


def is_cookie_session(session: EnjiApiSession) -> bool:
    return session.stored_auth["credential"]["type"] == CredentialType.COOKIE.value


def normalize_json_object(payload: object) -> JsonObjectPayload:
    return _normalize_json_object(payload)


def _render_api_path(path_template: str, path_params: ApiPathParams | None = None) -> str:
    if not path_params:
        return path_template
    path = path_template
    for key, value in path_params.items():
        path = path.replace(f"{{{key}}}", quote(value, safe=""))
    return path


def _normalize_json_object(payload: object) -> JsonObjectPayload:
    if not isinstance(payload, dict):
        return {}
    return {key: _normalize_json_value(value) for key, value in payload.items() if isinstance(key, str)}


def _normalize_json_list(payload: object) -> list[JsonValue]:
    if not isinstance(payload, list):
        return []
    return [_normalize_json_value(item) for item in payload]


def _normalize_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return _normalize_json_object(value)
    if isinstance(value, list):
        return _normalize_json_list(value)
    return None


def as_dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
