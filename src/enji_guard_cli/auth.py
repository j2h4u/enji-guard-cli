import asyncio
import contextlib
import fcntl
import logging
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict, TypeGuard, cast

from enji_guard_cli.auth_impl import auto_refresh as auto_refresh_impl
from enji_guard_cli.auth_impl.cookies import (
    CookieHeader as CookieHeader,
)
from enji_guard_cli.auth_impl.cookies import (
    cookie_value,
    jwt_expires_at,
    merge_set_cookie_headers,
    normalize_cookie_header,
    set_cookie_names,
    should_persist_transient_refresh_cookies,
)
from enji_guard_cli.auth_impl.payloads import (
    AuthRefreshPayload,
    AuthStatusPayload,
    _authenticated_payload,
    _profile_from_response,
    _unauthenticated_payload,
)
from enji_guard_cli.auth_impl.payloads import (
    _auth_refresh_payload as _pure_auth_refresh_payload,
)
from enji_guard_cli.auth_impl.store import (
    CredentialType,
    StoredAuth,
    load_auth_file,
    stored_auth,
    write_auth_file,
)
from enji_guard_cli.auth_impl.store import (
    replace_cookie_credential as store_replace_cookie_credential,
)
from enji_guard_cli.readiness import BackendReadinessProbe
from enji_guard_cli.settings import DEFAULT_BASE_URL, AutoRefreshSettings, default_settings
from enji_guard_cli.telemetry import log_event
from enji_guard_cli.transport import (
    EnjiHttpClient,
    EnjiHttpError,
    EnjiHttpRequest,
    EnjiHttpResponse,
    HttpxEnjiHttpClient,
    raise_for_response_status,
)

AUTH_REFRESH_PATH = "/api/v1/auth/refresh"
AUTH_INVALID_CODE = "AUTH_INVALID"
AUTH_REFRESH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_AUTH_FAILURE_CODES = frozenset({401, 403})
HTTP_TRANSIENT_REFRESH_ERROR_CODES = frozenset({429, 500, 502, 503, 504})
_LOGGER = logging.getLogger(__name__)
_COOKIE_REFRESH_LOCK = asyncio.Lock()


class AuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ImportCredentialPayload(TypedDict):
    ok: bool
    auth_file: str
    credential_type: str
    cookie_count: NotRequired[int]


def default_auth_file() -> Path:
    return default_settings().auth.auth_file


def normalize_bearer_token(raw_token: str) -> str:
    token = raw_token.strip()
    if token.lower().startswith("authorization:"):
        token = token.split(":", 1)[1].strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    if not token:
        raise ValueError("token input is empty")
    return token


def import_cookie(
    raw_cookie: str, auth_file: Path | None = None, base_url: str = DEFAULT_BASE_URL
) -> ImportCredentialPayload:
    cookie_header = normalize_cookie_header(raw_cookie)
    target = auth_file if auth_file is not None else default_auth_file()
    write_auth_file(
        target,
        stored_auth(base_url, {"type": "cookie", "cookie_header": cookie_header.value}),
    )
    return {
        "ok": True,
        "auth_file": str(target),
        "credential_type": CredentialType.COOKIE.value,
        "cookie_count": cookie_header.count,
    }


def import_bearer_token(
    raw_token: str,
    auth_file: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> ImportCredentialPayload:
    target = auth_file if auth_file is not None else default_auth_file()
    write_auth_file(
        target,
        stored_auth(base_url, {"type": "bearer_token", "token": normalize_bearer_token(raw_token)}),
    )
    return {
        "ok": True,
        "auth_file": str(target),
        "credential_type": CredentialType.BEARER_TOKEN.value,
    }


def auth_headers(stored_auth: StoredAuth) -> dict[str, str]:
    credential = stored_auth["credential"]
    if credential["type"] == CredentialType.COOKIE.value:
        return {"Cookie": credential["cookie_header"]}
    return {"Authorization": f"Bearer {credential['token']}"}


def load_stored_auth(path: Path) -> StoredAuth | None:
    return load_auth_file(path)


def cookie_access_expires_at(stored_auth: StoredAuth) -> datetime | None:
    credential = stored_auth["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        return None
    access_token = cookie_value(credential["cookie_header"], "access_token")
    if access_token is None:
        return None
    return jwt_expires_at(access_token)


def cookie_refresh_sleep_seconds(
    stored_auth: StoredAuth,
    now: datetime,
    *,
    settings: AutoRefreshSettings | None = None,
) -> int:
    refresh_settings = settings if settings is not None else default_settings().auto_refresh
    expires_at = cookie_access_expires_at(stored_auth)
    if expires_at is None:
        return refresh_settings.fallback_seconds
    refresh_at_delta = int((expires_at - now).total_seconds()) - refresh_settings.lead_seconds
    return max(refresh_at_delta, 0)


def replace_cookie_credential(path: Path, stored_auth: StoredAuth, cookie_header: str) -> StoredAuth:
    return store_replace_cookie_credential(path, stored_auth, cookie_header)


def auth_status(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AuthStatusPayload:
    return asyncio.run(auth_status_async(auth_file, client))


def refresh_auth(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AuthRefreshPayload:
    return asyncio.run(refresh_auth_async(auth_file, client))


async def auth_status_async(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AuthStatusPayload:
    target = auth_file if auth_file is not None else default_auth_file()
    if not target.exists():
        return _unauthenticated_payload(target, None, "AUTH_REQUIRED", "auth file does not exist")

    stored_auth = load_auth_file(target)
    if stored_auth is None:
        return _unauthenticated_payload(target, None, "AUTH_REQUIRED", "auth file is invalid")

    if client is not None:
        return await _auth_status_with_client(target, stored_auth, client)

    async with HttpxEnjiHttpClient() as owned_client:
        return await _auth_status_with_client(target, stored_auth, owned_client)


async def refresh_auth_async(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AuthRefreshPayload:
    target = auth_file if auth_file is not None else default_auth_file()
    try:
        if client is not None:
            refreshed_auth = await refresh_stored_cookie_auth(target, client)
            return _auth_refresh_payload(target, refreshed_auth)

        async with HttpxEnjiHttpClient() as owned_client:
            refreshed_auth = await refresh_stored_cookie_auth(target, owned_client)
            return _auth_refresh_payload(target, refreshed_auth)
    except EnjiHttpError as exc:
        raise AuthError(exc.code, exc.message) from exc


async def backend_readiness_probe_async(
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> BackendReadinessProbe:
    started_at = time.monotonic()
    target = auth_file if auth_file is not None else default_auth_file()
    if not target.exists():
        return _backend_readiness_failure(
            started_at,
            BackendReadinessProbe(
                ready=False,
                failure_kind="storage",
                failure_code="AUTH_REQUIRED",
                failure_message="auth file does not exist",
            ),
        )

    stored_auth = load_auth_file(target)
    if stored_auth is None:
        return _backend_readiness_failure(
            started_at,
            BackendReadinessProbe(
                ready=False,
                failure_kind="storage",
                failure_code="AUTH_REQUIRED",
                failure_message="auth file is invalid",
            ),
        )

    if client is not None:
        return await _backend_readiness_probe_with_client(stored_auth, client, started_at=started_at)

    settings = default_settings()
    async with HttpxEnjiHttpClient() as owned_client:
        return await _backend_readiness_probe_with_client(
            stored_auth,
            owned_client,
            started_at=started_at,
            timeout_seconds=settings.readiness.heartbeat_timeout_seconds,
        )


def start_auto_refresh_task() -> asyncio.Task[None] | None:
    settings = default_settings()
    return auto_refresh_impl.start_auto_refresh_task(
        auth_file=settings.auth.auth_file,
        refresh_settings=settings.auto_refresh,
        credential_cookie_type=CredentialType.COOKIE.value,
        dependencies=auto_refresh_impl.AutoRefreshTaskDependencies(
            load_stored_auth_fn=load_stored_auth,
            auto_refresh_loop_fn=auto_refresh_impl._auto_refresh_loop,
            loop_dependencies=auto_refresh_impl.AutoRefreshLoopDependencies(
                sleep_seconds_fn=auto_refresh_impl._auto_refresh_sleep_seconds,
                load_sleep_seconds_stored_auth_fn=load_stored_auth,
                cookie_refresh_sleep_seconds_fn=cookie_refresh_sleep_seconds,
                refresh_stored_cookie_auth_fn=_refresh_stored_cookie_auth_for_auto_refresh,
                cookie_access_expires_at_fn=cookie_access_expires_at,
                is_refresh_error_fn=_is_auto_refresh_error,
                log_event_fn=log_event,
                logger=_LOGGER,
                sleep_fn=asyncio.sleep,
                client_factory=HttpxEnjiHttpClient,
            ),
        ),
    )


async def _refresh_stored_cookie_auth_for_auto_refresh(path: Path, client: object) -> StoredAuth:
    return await refresh_stored_cookie_auth(path, cast(EnjiHttpClient, client))


def _is_auto_refresh_error(exc: Exception) -> TypeGuard[auto_refresh_impl.RefreshErrorLike]:
    return isinstance(exc, EnjiHttpError)


async def _auth_status_with_client(
    target: Path,
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
) -> AuthStatusPayload:
    credential_type = stored_auth["credential"]["type"]
    try:
        response = await _request_auth_status(stored_auth, client)
    except EnjiHttpError as exc:
        return _unauthenticated_payload(target, credential_type, exc.code, exc.message)

    if response.status_code == HTTP_OK:
        return _authenticated_payload(target, credential_type, _profile_from_response(response))
    if is_auth_invalid_response(response) and credential_type == CredentialType.COOKIE.value:
        return await _auth_status_after_refresh(target, stored_auth, client)
    return _auth_status_payload_from_response(
        target,
        credential_type,
        response,
        auth_invalid_code="AUTH_REQUIRED",
        auth_invalid_message="stored credential is not authenticated",
    )


def _auth_status_payload_from_response(
    target: Path,
    credential_type: str,
    response: EnjiHttpResponse,
    *,
    auth_invalid_code: str,
    auth_invalid_message: str,
) -> AuthStatusPayload:
    if response.status_code == HTTP_OK:
        return _authenticated_payload(target, credential_type, _profile_from_response(response))
    if is_auth_invalid_response(response):
        return _unauthenticated_payload(target, credential_type, auth_invalid_code, auth_invalid_message)
    if response.status_code in HTTP_AUTH_FAILURE_CODES:
        return _unauthenticated_payload(
            target, credential_type, "AUTH_REQUIRED", "stored credential is not authenticated"
        )
    try:
        raise_for_response_status(
            response,
            operation="auth status",
            expected_statuses=HTTP_AUTH_FAILURE_CODES | {HTTP_OK},
        )
    except EnjiHttpError as exc:
        return _unauthenticated_payload(target, credential_type, exc.code, exc.message)
    return _unauthenticated_payload(target, credential_type, "UPSTREAM", "auth status failed")


async def _auth_status_after_refresh(
    target: Path,
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
) -> AuthStatusPayload:
    try:
        refreshed_auth = await refresh_cookie_auth(target, stored_auth, client)
        response = await _request_auth_status(refreshed_auth, client)
    except EnjiHttpError as exc:
        return _unauthenticated_payload(target, CredentialType.COOKIE.value, exc.code, exc.message)
    return _auth_status_payload_from_response(
        target,
        CredentialType.COOKIE.value,
        response,
        auth_invalid_code=AUTH_INVALID_CODE,
        auth_invalid_message="invalid access token after refresh",
    )


async def _backend_readiness_probe_with_client(
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
    *,
    started_at: float,
    timeout_seconds: float | None = None,
) -> BackendReadinessProbe:
    credential_type = stored_auth["credential"]["type"]
    try:
        response = await _request_auth_status(stored_auth, client, timeout_seconds=timeout_seconds)
    except EnjiHttpError as exc:
        return _backend_readiness_failure(
            started_at,
            BackendReadinessProbe(
                ready=False,
                failure_kind="upstream",
                failure_code=exc.code,
                failure_message=exc.message,
                failure_status_code=exc.status_code,
                credential_type=credential_type,
            ),
        )
    if response.status_code == HTTP_OK:
        return BackendReadinessProbe(
            ready=True,
            credential_type=credential_type,
            elapsed_ms=_elapsed_ms(started_at),
        )
    if response.status_code in HTTP_AUTH_FAILURE_CODES or is_auth_invalid_response(response):
        return _backend_readiness_failure(
            started_at,
            BackendReadinessProbe(
                ready=False,
                failure_kind="auth",
                failure_code=AUTH_INVALID_CODE if is_auth_invalid_response(response) else "AUTH_REQUIRED",
                failure_message="stored credential is not authenticated",
                failure_status_code=response.status_code,
                credential_type=credential_type,
            ),
        )
    try:
        raise_for_response_status(
            response,
            operation="backend readiness",
            expected_statuses=HTTP_AUTH_FAILURE_CODES | {HTTP_OK},
        )
    except EnjiHttpError as exc:
        return _backend_readiness_failure(
            started_at,
            BackendReadinessProbe(
                ready=False,
                failure_kind="upstream",
                failure_code=exc.code,
                failure_message=exc.message,
                failure_status_code=exc.status_code,
                credential_type=credential_type,
            ),
        )
    return _backend_readiness_failure(
        started_at,
        BackendReadinessProbe(
            ready=False,
            failure_kind="upstream",
            failure_code="UPSTREAM",
            failure_message="backend readiness failed",
            failure_status_code=response.status_code,
            credential_type=credential_type,
        ),
    )


async def refresh_cookie_auth(path: Path, stored_auth: StoredAuth, client: EnjiHttpClient) -> StoredAuth:
    async with _COOKIE_REFRESH_LOCK:
        with _cookie_refresh_file_lock(path):
            latest_auth = _latest_auth_for_refresh(path, stored_auth)
            if latest_auth is not stored_auth:
                return latest_auth
            return await _refresh_cookie_auth_unlocked(path, stored_auth, client)


async def refresh_stored_cookie_auth(path: Path, client: EnjiHttpClient) -> StoredAuth:
    stored_auth = load_stored_auth(path)
    if stored_auth is None:
        raise EnjiHttpError("AUTH_REQUIRED", "auth file is invalid")
    return await refresh_cookie_auth(path, stored_auth, client)


async def _refresh_cookie_auth_unlocked(path: Path, stored_auth: StoredAuth, client: EnjiHttpClient) -> StoredAuth:
    credential = stored_auth["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        raise EnjiHttpError("AUTH_REQUIRED", "stored credential is not cookie based")
    response = await client.request(
        EnjiHttpRequest(
            method="POST",
            url=f"{stored_auth['base_url']}{AUTH_REFRESH_PATH}",
            operation="auth refresh",
            headers=_auth_refresh_headers(stored_auth),
        )
    )
    if response.status_code in {HTTP_UNAUTHORIZED, HTTP_FORBIDDEN}:
        raise EnjiHttpError(
            "AUTH_REQUIRED", "stored refresh cookie is not authenticated", status_code=response.status_code
        )
    _log_refresh_set_cookie_names(response)
    _validate_successful_refresh_cookie_rotation(response)
    refreshed_auth = _persist_refresh_response_cookies(path, stored_auth, response)
    raise_for_response_status(response, operation="auth refresh", expected_statuses={HTTP_OK})
    if not response.set_cookie_headers:
        raise EnjiHttpError("UPSTREAM", "auth refresh did not return Set-Cookie")
    return refreshed_auth


def _validate_successful_refresh_cookie_rotation(response: EnjiHttpResponse) -> None:
    if response.status_code != HTTP_OK:
        return
    names = set_cookie_names(response.set_cookie_headers)
    if "access_token" not in names:
        raise EnjiHttpError("UPSTREAM", "auth refresh did not return access_token Set-Cookie")
    if "refresh_token" not in names:
        raise EnjiHttpError("UPSTREAM", "auth refresh did not return refresh_token Set-Cookie")


def _log_refresh_set_cookie_names(response: EnjiHttpResponse) -> None:
    if response.status_code != HTTP_OK:
        return
    names = set_cookie_names(response.set_cookie_headers)
    log_event(
        _LOGGER,
        logging.INFO,
        "enji_auth_refresh_set_cookie_received",
        {"set_cookie_names": ",".join(names), "set_cookie_count": len(names)},
    )


def _persist_refresh_response_cookies(
    path: Path,
    stored_auth: StoredAuth,
    response: EnjiHttpResponse,
) -> StoredAuth:
    if not response.set_cookie_headers:
        return stored_auth
    if response.status_code != HTTP_OK and not _should_persist_transient_refresh_cookies(response):
        return stored_auth
    credential = stored_auth["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        raise EnjiHttpError("AUTH_REQUIRED", "stored credential is not cookie based")
    try:
        cookie_header = merge_set_cookie_headers(credential["cookie_header"], response.set_cookie_headers)
        return replace_cookie_credential(path, stored_auth, cookie_header.value)
    except OSError as exc:
        raise EnjiHttpError("STORAGE", f"failed to persist refreshed cookie: {exc}") from exc
    except ValueError as exc:
        raise EnjiHttpError("AUTH_REQUIRED", str(exc)) from exc


def _should_persist_transient_refresh_cookies(response: EnjiHttpResponse) -> bool:
    return should_persist_transient_refresh_cookies(
        response.status_code,
        HTTP_TRANSIENT_REFRESH_ERROR_CODES,
        response.set_cookie_headers,
    )


def _latest_auth_for_refresh(path: Path, stored_auth: StoredAuth) -> StoredAuth:
    latest_auth = load_stored_auth(path)
    if latest_auth is None:
        return stored_auth

    latest_credential = latest_auth["credential"]
    stored_credential = stored_auth["credential"]
    if latest_credential["type"] != CredentialType.COOKIE.value:
        return latest_auth
    if stored_credential["type"] != CredentialType.COOKIE.value:
        return latest_auth
    if latest_credential["cookie_header"] != stored_credential["cookie_header"]:
        return latest_auth
    return stored_auth


@contextlib.contextmanager
def _cookie_refresh_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        lock_path.chmod(0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def is_auth_invalid_response(response: EnjiHttpResponse) -> bool:
    if response.status_code != HTTP_UNAUTHORIZED:
        return False
    try:
        payload = response.json(operation="auth invalid check")
    except EnjiHttpError:
        return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if isinstance(error, dict):
        return error.get("code") == AUTH_INVALID_CODE
    return payload.get("code") == AUTH_INVALID_CODE


def _auth_refresh_headers(stored_auth: StoredAuth) -> dict[str, str]:
    settings = default_settings().auth
    headers = auth_headers(stored_auth)
    headers.update(
        {
            "Origin": settings.guard_origin,
            "Referer": settings.guard_referer,
            "User-Agent": AUTH_REFRESH_USER_AGENT,
        }
    )
    return headers


def _auth_refresh_payload(auth_file: Path, stored_auth: StoredAuth) -> AuthRefreshPayload:
    try:
        return _pure_auth_refresh_payload(auth_file, stored_auth)
    except ValueError as exc:
        raise EnjiHttpError("AUTH_REQUIRED", str(exc)) from exc


async def _request_auth_status(
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
    *,
    timeout_seconds: float | None = None,
) -> EnjiHttpResponse:
    request_timeout = timeout_seconds if timeout_seconds is not None else default_settings().transport.timeout_seconds
    return await client.request(
        EnjiHttpRequest(
            method="GET",
            url=f"{stored_auth['base_url']}/api/v1/auth/me",
            headers=auth_headers(stored_auth),
            timeout_seconds=request_timeout,
            operation="auth status",
        )
    )


def _backend_readiness_failure(started_at: float, probe: BackendReadinessProbe) -> BackendReadinessProbe:
    return BackendReadinessProbe(
        ready=False,
        failure_kind=probe.failure_kind,
        failure_code=probe.failure_code,
        failure_message=probe.failure_message,
        failure_status_code=probe.failure_status_code,
        credential_type=probe.credential_type,
        elapsed_ms=_elapsed_ms(started_at),
    )


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)
