import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from enji_guard_cli.auth_session import auto_refresh as auto_refresh_impl
from enji_guard_cli.auth_session.auth_protocol import (
    AUTH_INVALID_CODE,
    HTTP_AUTH_FAILURE_CODES,
    HTTP_OK,
    is_auth_invalid_response,
)
from enji_guard_cli.auth_session.cookies import (
    cookie_value,
    jwt_expires_at,
    normalize_cookie_header,
)
from enji_guard_cli.auth_session.coordinator import (
    CoordinatorDependencies,
    RefreshCoordinator,
    import_credential,
)
from enji_guard_cli.auth_session.credential_changes import credential_changes
from enji_guard_cli.auth_session.models import AuthBackendReadinessResult
from enji_guard_cli.auth_session.payloads import (
    AuthPayloadRenewal,
    AuthStatusPayload,
    _authenticated_payload,
    _profile_from_response,
    _unauthenticated_payload,
)
from enji_guard_cli.auth_session.ports import AuthEventSink, AuthOutcomeSink
from enji_guard_cli.auth_session.projection import (
    AuthProjectionError,
    RefreshProjectionStatus,
    network_credential,
    project_auth,
    refresh_projection_status,
)
from enji_guard_cli.auth_session.store import (
    CredentialType,
    StoredAuth,
    load_auth,
    load_journal,
    stored_auth,
)
from enji_guard_cli.settings import (
    DEFAULT_BASE_URL,
    AuthSettings,
    AutoRefreshSettings,
    EnjiGuardSettings,
    default_settings,
)
from enji_guard_cli.transport import (
    EnjiHttpClient,
    EnjiHttpError,
    EnjiHttpRequest,
    EnjiHttpResponse,
    HttpxEnjiHttpClient,
    discard_transport_event,
    raise_for_response_status,
)
from enji_guard_cli.transport_types import RetryProfile

AUTH_REFRESH_PATH = "/api/v1/auth/refresh"
AUTH_REFRESH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
_LOGGER = logging.getLogger(__name__)


def _noop_event_sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> None:
    _ = logger, level, event, fields


def _event_sink_or_noop(event_sink: AuthEventSink | None) -> AuthEventSink:
    return event_sink if event_sink is not None else _noop_event_sink


def _auth_payload_renewal(refresh_status: RefreshProjectionStatus) -> AuthPayloadRenewal:
    return AuthPayloadRenewal(
        refresh_state=refresh_status.refresh_state,
        reauth_required=refresh_status.reauth_required,
        message=refresh_status.message,
    )


def _reauth_payload_renewal(refresh_status: RefreshProjectionStatus) -> AuthPayloadRenewal:
    return AuthPayloadRenewal(refresh_state=refresh_status.refresh_state, reauth_required=True)


class AuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AuthInvalidStatus:
    code: str
    message: str


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
    import_credential(target, stored_auth(base_url, {"type": "cookie", "cookie_header": cookie_header.value}))
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
    import_credential(
        target, stored_auth(base_url, {"type": "bearer_token", "token": normalize_bearer_token(raw_token)})
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


def auth_status(
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> AuthStatusPayload:
    return asyncio.run(auth_status_async(auth_file, client))


async def auth_status_async(
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> AuthStatusPayload:
    target = auth_file if auth_file is not None else default_auth_file()
    projection = _auth_projection(target)
    refresh_status = refresh_projection_status(projection)
    try:
        stored_auth = network_credential(projection)
    except AuthProjectionError as exc:
        return _unauthenticated_payload(
            target,
            None,
            exc.code,
            exc.message,
            renewal=_reauth_payload_renewal(refresh_status),
        )

    if client is not None:
        return await _auth_status_with_client(target, stored_auth, client, refresh_status=refresh_status)

    async with HttpxEnjiHttpClient(event_sink=discard_transport_event) as owned_client:
        return await _auth_status_with_client(target, stored_auth, owned_client, refresh_status=refresh_status)


async def backend_readiness_probe_async(
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> AuthBackendReadinessResult:
    started_at = time.monotonic()
    target = auth_file if auth_file is not None else default_auth_file()
    projection = _auth_projection(target)
    refresh_status = refresh_projection_status(projection)
    try:
        stored_auth = network_credential(projection)
    except AuthProjectionError as exc:
        return _backend_readiness_failure(
            started_at,
            AuthBackendReadinessResult(
                ready=False,
                failure_kind="storage",
                failure_code=exc.code,
                failure_message=exc.message,
                refresh_state=refresh_status.refresh_state,
                reauth_required=True,
                bypass_grace=True,
            ),
        )

    if client is not None:
        return await _backend_readiness_probe_with_client(
            stored_auth,
            client,
            started_at=started_at,
            refresh_status=refresh_status,
        )

    settings = default_settings()
    async with HttpxEnjiHttpClient(event_sink=discard_transport_event) as owned_client:
        return await _backend_readiness_probe_with_client(
            stored_auth,
            owned_client,
            started_at=started_at,
            timeout_seconds=settings.readiness.heartbeat_timeout_seconds,
            refresh_status=refresh_status,
        )


def start_auto_refresh_task(
    auth_file: Path | None = None,
    *,
    settings: EnjiGuardSettings | None = None,
    event_sink: AuthEventSink | None = None,
    outcome_sink: AuthOutcomeSink | None = None,
) -> asyncio.Task[None] | None:
    resolved_settings = settings if settings is not None else default_settings()
    resolved_auth_file = auth_file if auth_file is not None else resolved_settings.auth.auth_file
    resolved_event_sink = _event_sink_or_noop(event_sink)
    return auto_refresh_impl.start_auto_refresh_task(
        auth_file=resolved_auth_file,
        refresh_settings=resolved_settings.auto_refresh,
        dependencies=auto_refresh_impl.AutoRefreshTaskDependencies(
            auto_refresh_loop_fn=auto_refresh_impl._auto_refresh_loop,
            loop_dependencies=auto_refresh_impl.AutoRefreshLoopDependencies(
                load_auth_fn=load_auth,
                cookie_refresh_sleep_seconds_fn=cookie_refresh_sleep_seconds,
                refresh_cookie_auth_fn=lambda path, auth, client: _refresh_cookie_auth(
                    path,
                    auth,
                    cast(EnjiHttpClient, client),
                    settings=resolved_settings,
                    dependencies=CoordinatorDependencies(
                        event_sink=resolved_event_sink,
                        outcome_sink=outcome_sink,
                    ),
                ),
                log_event_fn=resolved_event_sink,
                logger=_LOGGER,
                sleep_fn=asyncio.sleep,
                client_factory=lambda: HttpxEnjiHttpClient(event_sink=resolved_event_sink),
                credential_changes_fn=credential_changes,
            ),
        ),
    )


async def reconcile_auth_startup(
    auth_file: Path | None = None,
    *,
    outcome_sink: AuthOutcomeSink | None = None,
) -> None:
    """Durably reconcile abandoned rotations before runtime observers start."""

    target = auth_file if auth_file is not None else default_auth_file()

    class _StartupOnlyExchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            raise AssertionError("startup reconciliation must not dispatch refresh")

    await RefreshCoordinator(
        target,
        _StartupOnlyExchange(),
        dependencies=CoordinatorDependencies(outcome_sink=outcome_sink),
    ).recover_startup()


async def _auth_status_with_client(
    target: Path,
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
    *,
    refresh_status: RefreshProjectionStatus,
) -> AuthStatusPayload:
    credential_type = stored_auth["credential"]["type"]
    try:
        response = await _request_auth_status(stored_auth, client)
    except EnjiHttpError as exc:
        return _unauthenticated_payload(
            target,
            credential_type,
            exc.code,
            exc.message,
            renewal=_auth_payload_renewal(refresh_status),
        )

    if response.status_code == HTTP_OK:
        return _authenticated_payload(
            target,
            credential_type,
            _profile_from_response(response),
            renewal=_auth_payload_renewal(refresh_status),
        )
    return _auth_status_payload_from_response(
        target,
        credential_type,
        response,
        auth_invalid=AuthInvalidStatus("AUTH_REQUIRED", "stored credential is not authenticated"),
        refresh_status=refresh_status,
    )


def _auth_status_payload_from_response(
    target: Path,
    credential_type: str,
    response: EnjiHttpResponse,
    *,
    auth_invalid: AuthInvalidStatus,
    refresh_status: RefreshProjectionStatus,
) -> AuthStatusPayload:
    if response.status_code == HTTP_OK:
        return _authenticated_payload(
            target,
            credential_type,
            _profile_from_response(response),
            renewal=_auth_payload_renewal(refresh_status),
        )
    if is_auth_invalid_response(response):
        return _unauthenticated_payload(
            target,
            credential_type,
            auth_invalid.code,
            auth_invalid.message,
            renewal=_reauth_payload_renewal(refresh_status),
        )
    if response.status_code in HTTP_AUTH_FAILURE_CODES:
        return _unauthenticated_payload(
            target,
            credential_type,
            "AUTH_REQUIRED",
            "stored credential is not authenticated",
            renewal=_reauth_payload_renewal(refresh_status),
        )
    try:
        raise_for_response_status(
            response,
            operation="auth status",
            expected_statuses=HTTP_AUTH_FAILURE_CODES | {HTTP_OK},
        )
    except EnjiHttpError as exc:
        return _unauthenticated_payload(
            target,
            credential_type,
            exc.code,
            exc.message,
            renewal=_auth_payload_renewal(refresh_status),
        )
    return _unauthenticated_payload(
        target,
        credential_type,
        "UPSTREAM",
        "auth status failed",
        renewal=_auth_payload_renewal(refresh_status),
    )


async def _backend_readiness_probe_with_client(
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
    *,
    started_at: float,
    timeout_seconds: float | None = None,
    refresh_status: RefreshProjectionStatus,
) -> AuthBackendReadinessResult:
    credential_type = stored_auth["credential"]["type"]
    try:
        response = await _request_auth_status(stored_auth, client, timeout_seconds=timeout_seconds)
    except EnjiHttpError as exc:
        return _backend_readiness_failure(
            started_at,
            AuthBackendReadinessResult(
                ready=False,
                failure_kind="upstream",
                failure_code=exc.code,
                failure_message=exc.message,
                failure_status_code=exc.status_code,
                credential_type=credential_type,
                refresh_state=refresh_status.refresh_state,
                reauth_required=refresh_status.reauth_required,
            ),
        )
    if response.status_code == HTTP_OK:
        return AuthBackendReadinessResult(
            ready=True,
            credential_type=credential_type,
            refresh_state=refresh_status.refresh_state,
            reauth_required=refresh_status.reauth_required,
            elapsed_ms=_elapsed_ms(started_at),
        )
    if response.status_code in HTTP_AUTH_FAILURE_CODES or is_auth_invalid_response(response):
        return _backend_readiness_failure(
            started_at,
            AuthBackendReadinessResult(
                ready=False,
                failure_kind="auth",
                failure_code=AUTH_INVALID_CODE if is_auth_invalid_response(response) else "AUTH_REQUIRED",
                failure_message="stored credential is not authenticated",
                failure_status_code=response.status_code,
                credential_type=credential_type,
                refresh_state=refresh_status.refresh_state,
                reauth_required=True,
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
            AuthBackendReadinessResult(
                ready=False,
                failure_kind="upstream",
                failure_code=exc.code,
                failure_message=exc.message,
                failure_status_code=exc.status_code,
                credential_type=credential_type,
                refresh_state=refresh_status.refresh_state,
                reauth_required=refresh_status.reauth_required,
            ),
        )
    return _backend_readiness_failure(
        started_at,
        AuthBackendReadinessResult(
            ready=False,
            failure_kind="upstream",
            failure_code="UPSTREAM",
            failure_message="backend readiness failed",
            failure_status_code=response.status_code,
            credential_type=credential_type,
            refresh_state=refresh_status.refresh_state,
            reauth_required=refresh_status.reauth_required,
        ),
    )


async def _refresh_cookie_auth(
    path: Path,
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
    *,
    settings: EnjiGuardSettings | None = None,
    dependencies: CoordinatorDependencies | None = None,
) -> StoredAuth:
    auth_settings = (settings if settings is not None else default_settings()).auth

    class _HttpxRefreshExchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            return await client.request(
                EnjiHttpRequest(
                    method="POST",
                    url=f"{source['base_url']}{AUTH_REFRESH_PATH}",
                    operation="auth refresh",
                    headers=_auth_refresh_headers(source, settings=auth_settings),
                    profile=RetryProfile.AUTH_REFRESH,
                )
            )

    return await RefreshCoordinator(
        path,
        _HttpxRefreshExchange(),
        dependencies=dependencies,
    ).refresh(stored_auth)


def _auth_projection(path: Path):
    return project_auth(load_auth(path), load_journal(path))


def _auth_refresh_headers(stored_auth: StoredAuth, *, settings: AuthSettings) -> dict[str, str]:
    headers = auth_headers(stored_auth)
    headers.update(
        {
            "Origin": settings.guard_origin,
            "Referer": settings.guard_referer,
            "User-Agent": AUTH_REFRESH_USER_AGENT,
        }
    )
    return headers


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
            profile=RetryProfile.READ,
            timeout_seconds=request_timeout,
            operation="auth status",
        )
    )


def _backend_readiness_failure(started_at: float, probe: AuthBackendReadinessResult) -> AuthBackendReadinessResult:
    return AuthBackendReadinessResult(
        ready=False,
        failure_kind=probe.failure_kind,
        failure_code=probe.failure_code,
        failure_message=probe.failure_message,
        failure_status_code=probe.failure_status_code,
        credential_type=probe.credential_type,
        refresh_state=probe.refresh_state,
        reauth_required=probe.reauth_required,
        elapsed_ms=_elapsed_ms(started_at),
        bypass_grace=probe.bypass_grace,
    )


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)
