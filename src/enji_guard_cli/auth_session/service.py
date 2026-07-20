"""Application service for Auth Session lifecycle operations."""

import asyncio
from pathlib import Path

from enji_guard_cli.auth_session import api as _api
from enji_guard_cli.auth_session.models import (
    AuthBackendReadinessResult,
    AuthSessionStatus,
    AuthStatusPayload,
    ImportCredentialPayload,
    StoredAuth,
)
from enji_guard_cli.auth_session.ports import AuthEventSink
from enji_guard_cli.settings import EnjiGuardSettings, default_settings
from enji_guard_cli.transport import EnjiHttpClient


class AuthSessionService:
    """Thin composition seam around credential storage and refresh policy."""

    def __init__(
        self,
        auth_file: Path | None = None,
        client: EnjiHttpClient | None = None,
        *,
        settings: EnjiGuardSettings | None = None,
        event_sink: AuthEventSink | None = None,
    ) -> None:
        resolved_settings = settings if settings is not None else default_settings()
        self.auth_file = auth_file if auth_file is not None else resolved_settings.auth.auth_file
        self.client = client
        self.settings = resolved_settings
        self.event_sink = event_sink

    def import_cookie(self, raw_cookie: str) -> ImportCredentialPayload:
        return _api.import_cookie(raw_cookie, self.auth_file)

    def import_bearer_token(self, raw_token: str) -> ImportCredentialPayload:
        return _api.import_bearer_token(raw_token, self.auth_file)

    def load(self) -> StoredAuth | None:
        target = self.auth_file if self.auth_file is not None else _api.default_auth_file()
        return _api.load_stored_auth(target)

    def auth_headers(self, stored_auth: StoredAuth | None = None) -> dict[str, str]:
        current = stored_auth if stored_auth is not None else self.load()
        if current is None:
            return {}
        return _api.auth_headers(current)

    async def status_async(self) -> AuthStatusPayload:
        return await _api.auth_status_async(self.auth_file, self.client, event_sink=self.event_sink)

    def status(self) -> AuthSessionStatus:
        return AuthSessionStatus.from_payload(asyncio.run(self.status_async()))

    async def status_result_async(self) -> AuthSessionStatus:
        return AuthSessionStatus.from_payload(await self.status_async())

    async def backend_readiness_probe_async(self) -> AuthBackendReadinessResult:
        """Observe backend auth state without triggering cookie refresh."""
        return await _api.backend_readiness_probe_async(self.auth_file, self.client)

    def start_auto_refresh_task(self):
        """Start the single supervisor-owned cookie refresh task."""
        return _api.start_auto_refresh_task(
            self.auth_file,
            settings=self.settings,
            event_sink=self.event_sink,
        )


def default_auth_file() -> Path:
    return _api.default_auth_file()


def import_cookie(raw_cookie: str, auth_file: Path | None = None) -> ImportCredentialPayload:
    return _api.import_cookie(raw_cookie, auth_file)


def import_bearer_token(raw_token: str, auth_file: Path | None = None) -> ImportCredentialPayload:
    return _api.import_bearer_token(raw_token, auth_file)


def auth_status(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AuthStatusPayload:
    return _api.auth_status(auth_file, client)


__all__ = [
    "AuthSessionService",
    "auth_status",
    "default_auth_file",
    "import_bearer_token",
    "import_cookie",
]
