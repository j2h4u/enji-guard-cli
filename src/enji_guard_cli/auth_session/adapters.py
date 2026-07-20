"""Composition adapters exposing Auth Session capabilities to other contexts."""

from collections.abc import AsyncGenerator
from pathlib import Path

from enji_guard_cli.auth_session import api as _api
from enji_guard_cli.auth_session.credential_changes import credential_changes
from enji_guard_cli.auth_session.models import AuthBackendReadinessResult, StoredAuth
from enji_guard_cli.auth_session.ports import AuthEventSink
from enji_guard_cli.enji_gateway.ports import GatewayAuthPort
from enji_guard_cli.runtime_observability.ports import BackendReadinessObservation, RuntimeAuthPort
from enji_guard_cli.settings import EnjiGuardSettings, default_settings
from enji_guard_cli.transport import EnjiHttpClient


class AuthSessionAdapter(GatewayAuthPort, RuntimeAuthPort):
    """Explicit composition adapter; implementation dependencies stop here."""

    def __init__(
        self,
        auth_file: Path | None = None,
        *,
        settings: EnjiGuardSettings | None = None,
        event_sink: AuthEventSink | None = None,
    ) -> None:
        self.settings = settings if settings is not None else default_settings()
        self.auth_file = auth_file if auth_file is not None else self.settings.auth.auth_file
        self.event_sink = event_sink

    def load(self, auth_file: Path | None = None) -> StoredAuth | None:
        target = auth_file if auth_file is not None else self.auth_file
        if target is None:
            target = _api.default_auth_file()
        return _api.load_stored_auth(target)

    def headers(self, stored_auth: StoredAuth) -> dict[str, str]:
        return _api.auth_headers(stored_auth)

    def is_cookie_session(self, stored_auth: StoredAuth) -> bool:
        return stored_auth["credential"]["type"] == _api.CredentialType.COOKIE.value

    async def refresh(self, auth_file: Path, stored_auth: StoredAuth, client: EnjiHttpClient) -> StoredAuth:
        return await _api.refresh_cookie_auth(auth_file, stored_auth, client, event_sink=self.event_sink)

    async def observe_backend_readiness(self) -> BackendReadinessObservation:
        result: AuthBackendReadinessResult = await _api.backend_readiness_probe_async(self.auth_file)
        return BackendReadinessObservation(
            ready=result.ready,
            failure_kind=result.failure_kind,
            failure_code=result.failure_code,
            failure_message=result.failure_message,
            failure_status_code=result.failure_status_code,
            credential_type=result.credential_type,
            elapsed_ms=result.elapsed_ms,
        )

    async def credential_changes(self) -> AsyncGenerator[None]:
        async for _ in credential_changes(self.auth_file):
            yield None

    def start_auto_refresh_task(self):
        return _api.start_auto_refresh_task(
            self.auth_file,
            settings=self.settings,
            event_sink=self.event_sink,
        )


__all__ = ["AuthSessionAdapter"]
