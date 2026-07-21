"""Narrow ports consumed by the gateway and runtime."""

from collections.abc import Mapping
from logging import Logger
from pathlib import Path
from typing import Protocol

from enji_guard_cli.auth_session.models import (
    AuthBackendReadinessResult,
    AuthSessionStatus,
    StoredAuth,
)


class AuthEventSink(Protocol):
    """Narrow callback for safe auth lifecycle events."""

    def __call__(self, logger: Logger, level: int, event: str, fields: Mapping[str, object]) -> None: ...


class AuthReadinessPort(Protocol):
    """Backend observation capability consumed by runtime composition."""

    async def backend_readiness_probe_async(self) -> AuthBackendReadinessResult: ...


class AuthRefreshTaskPort(Protocol):
    """Background refresh task capability consumed by runtime composition."""

    def start_auto_refresh_task(self): ...


class AuthSessionPort(Protocol):
    """Credential lifecycle operations; no delivery or endpoint controls."""

    def auth_headers(self, stored_auth: StoredAuth) -> dict[str, str]: ...

    def status(self) -> AuthSessionStatus: ...


class AuthStorePort(Protocol):
    def load(self, auth_file: Path) -> StoredAuth | None: ...

    def save(self, auth_file: Path, stored_auth: StoredAuth) -> None: ...


__all__ = ["AuthEventSink", "AuthReadinessPort", "AuthRefreshTaskPort", "AuthSessionPort", "AuthStorePort"]
