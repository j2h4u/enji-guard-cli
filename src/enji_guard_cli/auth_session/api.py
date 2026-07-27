import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from enji_guard_cli.auth_session import auto_refresh as auto_refresh_impl
from enji_guard_cli.auth_session.cookies import (
    cookie_value,
    jwt_expires_at,
    normalize_cookie_header,
)
from enji_guard_cli.auth_session.coordinator import (
    CoordinatorDependencies,
    RefreshCoordinator,
    adjudicate_source_revision_alive,
    import_credential,
)
from enji_guard_cli.auth_session.credential_changes import credential_changes
from enji_guard_cli.auth_session.models import AuthBackendReadinessResult
from enji_guard_cli.auth_session.payloads import (
    AuthStatusPayload,
    _authenticated_payload,
    _profile_from_response,
    _unauthenticated_payload,
)
from enji_guard_cli.auth_session.ports import AuthEventSink, AuthOutcomeSink
from enji_guard_cli.auth_session.projection import (
    AuthProjectionError,
    adjudication_credential,
    network_credential,
    project_auth,
)
from enji_guard_cli.auth_session.state_machine import OutcomeUnknown
from enji_guard_cli.auth_session.store import (
    CredentialType,
    StoredAuth,
    load_auth,
    load_journal,
    stored_auth,
)
from enji_guard_cli.settings import DEFAULT_BASE_URL, AutoRefreshSettings, EnjiGuardSettings, default_settings
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
AUTH_INVALID_CODE = "AUTH_INVALID"
AUTH_REFRESH_SUCCESSOR_LOST_CODE = "AUTH_REFRESH_SUCCESSOR_LOST"
AUTH_REFRESH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_AUTH_FAILURE_CODES = frozenset({401, 403})
_LOGGER = logging.getLogger(__name__)


def _noop_event_sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> None:
    _ = logger, level, event, fields


def _event_sink_or_noop(event_sink: AuthEventSink | None) -> AuthEventSink:
    return event_sink if event_sink is not None else _noop_event_sink


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
    try:
        stored_auth = network_credential(_auth_projection(target))
    except AuthProjectionError as exc:
        return _unauthenticated_payload(target, None, exc.code, exc.message)

    if client is not None:
        return await _auth_status_with_client(target, stored_auth, client)

    async with HttpxEnjiHttpClient(event_sink=discard_transport_event) as owned_client:
        return await _auth_status_with_client(target, stored_auth, owned_client)


async def backend_readiness_probe_async(
    auth_file: Path | None = None,
    client: EnjiHttpClient | None = None,
) -> AuthBackendReadinessResult:
    started_at = time.monotonic()
    target = auth_file if auth_file is not None else default_auth_file()
    projection = _auth_projection(target)
    try:
        stored_auth = network_credential(projection)
    except AuthProjectionError as exc:
        pending = adjudication_credential(projection)
        if pending is None:
            return _backend_readiness_failure(
                started_at,
                AuthBackendReadinessResult(
                    ready=False,
                    failure_kind="storage",
                    failure_code=exc.code,
                    failure_message=exc.message,
                    bypass_grace=True,
                ),
            )
        adjudication = _Adjudication(pending[0], pending[1], exc)
        return await _with_readiness_client(
            client,
            lambda active, timeout: _adjudicate_unknown_outcome(
                target, adjudication, active, started_at=started_at, timeout_seconds=timeout
            ),
        )

    return await _with_readiness_client(
        client,
        lambda active, timeout: _backend_readiness_probe_with_client(
            stored_auth, active, started_at=started_at, timeout_seconds=timeout
        ),
    )


async def _with_readiness_client(
    client: EnjiHttpClient | None,
    run: Callable[[EnjiHttpClient, float | None], Awaitable[AuthBackendReadinessResult]],
) -> AuthBackendReadinessResult:
    if client is not None:
        return await run(client, None)
    timeout_seconds = default_settings().readiness.heartbeat_timeout_seconds
    async with HttpxEnjiHttpClient(event_sink=discard_transport_event) as owned_client:
        return await run(owned_client, timeout_seconds)


@dataclass(frozen=True, slots=True)
class _Adjudication:
    """The credential to probe with, and the verdict it has to replace."""

    stored_auth: StoredAuth
    state: OutcomeUnknown
    unresolved: AuthProjectionError


async def _adjudicate_unknown_outcome(
    target: Path,
    adjudication: _Adjudication,
    client: EnjiHttpClient,
    *,
    started_at: float,
    timeout_seconds: float | None = None,
) -> AuthBackendReadinessResult:
    """Ask the backend what an ambiguous refresh actually did.

    This is a read of ``/api/v1/auth/me`` with the credential already on disk.
    The refresh token is not withheld -- it rides the stored ``Cookie`` header
    like every other cookie.  What makes this safe is the *endpoint*: a read
    that does not consume refresh tokens.  Point this at anything else and the
    safety argument is gone.

    A ``200`` is weaker evidence than it looks.  ``/api/v1/auth/me``
    authenticates the ``access_token`` JWT, which stays valid until its own
    expiry whether or not the refresh token was consumed -- and refresh is
    scheduled a lead window *before* that expiry, so the probe runs while the
    old JWT is still good in both worlds.  So ``200`` means "the access token
    still works", not "the rotation never landed".  If it did land, clearing
    here lets the next scheduled refresh re-send a consumed token and take a
    clean rejection; see ``docs/decisions.md`` for why that trade is bounded.
    """

    stored_auth = adjudication.stored_auth
    credential_type = stored_auth["credential"]["type"]
    try:
        response = await _request_auth_status(stored_auth, client, timeout_seconds=timeout_seconds)
    except EnjiHttpError as exc:
        return _unadjudicated(started_at, adjudication, credential_type, status_code=exc.status_code)

    if response.status_code == HTTP_OK:
        cleared = await asyncio.to_thread(adjudicate_source_revision_alive, target, adjudication.state.source_revision)
        if not cleared:
            # Storage moved under the probe.  Decide nothing and re-read the
            # durable state on the next tick.  This is a storage race, not an
            # upstream fault, and dashboards should not read it as one.
            return _unadjudicated(
                started_at,
                adjudication,
                credential_type,
                status_code=response.status_code,
                failure_kind="storage",
            )
        return AuthBackendReadinessResult(
            ready=True,
            credential_type=credential_type,
            elapsed_ms=_elapsed_ms(started_at),
        )

    if response.status_code in HTTP_AUTH_FAILURE_CODES or is_auth_invalid_response(response):
        # The source credential is dead: the rotation landed and its successor
        # was lost with the ambiguous response.  Only an import recovers this,
        # and it gets its own code so alerting can tell a proven-terminal state
        # from one that is merely still waiting on the backend.
        return _backend_readiness_failure(
            started_at,
            AuthBackendReadinessResult(
                ready=False,
                failure_kind="storage",
                failure_code=AUTH_REFRESH_SUCCESSOR_LOST_CODE,
                failure_message=(
                    "refresh rotated the credential and its replacement was lost; import a fresh browser credential"
                ),
                failure_status_code=response.status_code,
                credential_type=credential_type,
                bypass_grace=True,
            ),
        )
    return _unadjudicated(started_at, adjudication, credential_type, status_code=response.status_code)


def _unadjudicated(
    started_at: float,
    adjudication: _Adjudication,
    credential_type: str,
    *,
    status_code: int | None,
    failure_kind: str = "upstream",
) -> AuthBackendReadinessResult:
    """Stay unready without burning the credential on an unproven verdict.

    Deliberately not the projection's message: that one tells the operator to
    import a credential, which fixes nothing while the backend is unreachable.
    """

    return _backend_readiness_failure(
        started_at,
        AuthBackendReadinessResult(
            ready=False,
            failure_kind=failure_kind,
            failure_code=adjudication.unresolved.code,
            failure_message=(f"{adjudication.state.reason}; the adjudication probe has no answer yet, retrying"),
            failure_status_code=status_code,
            credential_type=credential_type,
        ),
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
                    path, auth, cast(EnjiHttpClient, client), outcome_sink=outcome_sink
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
) -> AuthStatusPayload:
    credential_type = stored_auth["credential"]["type"]
    try:
        response = await _request_auth_status(stored_auth, client)
    except EnjiHttpError as exc:
        return _unauthenticated_payload(target, credential_type, exc.code, exc.message)

    if response.status_code == HTTP_OK:
        return _authenticated_payload(target, credential_type, _profile_from_response(response))
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


async def _backend_readiness_probe_with_client(
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
    *,
    started_at: float,
    timeout_seconds: float | None = None,
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
            ),
        )
    if response.status_code == HTTP_OK:
        return AuthBackendReadinessResult(
            ready=True,
            credential_type=credential_type,
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
        ),
    )


async def _refresh_cookie_auth(
    path: Path,
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
    *,
    outcome_sink: AuthOutcomeSink | None = None,
) -> StoredAuth:

    class _HttpxRefreshExchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            return await client.request(
                EnjiHttpRequest(
                    method="POST",
                    url=f"{source['base_url']}{AUTH_REFRESH_PATH}",
                    operation="auth refresh",
                    headers=_auth_refresh_headers(source),
                    profile=RetryProfile.AUTH_REFRESH,
                )
            )

    return await RefreshCoordinator(
        path,
        _HttpxRefreshExchange(),
        dependencies=CoordinatorDependencies(outcome_sink=outcome_sink),
    ).refresh(stored_auth)


def _auth_projection(path: Path):
    return project_auth(load_auth(path), load_journal(path))


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
        elapsed_ms=_elapsed_ms(started_at),
        bypass_grace=probe.bypass_grace,
    )


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)
