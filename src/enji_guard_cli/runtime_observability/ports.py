"""Runtime-facing ports supplied by the composition root."""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BackendReadinessObservation:
    """Transport-neutral auth/backend observation consumed by runtime."""

    ready: bool
    failure_kind: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    failure_status_code: int | None = None
    credential_type: str | None = None
    elapsed_ms: int | None = None


class BackendReadinessObserver(Protocol):
    async def observe_backend_readiness(self) -> BackendReadinessObservation: ...


class BackgroundRefreshStarter(Protocol):
    def start_auto_refresh_task(self) -> asyncio.Task[None] | None: ...


class CredentialChangeObserver(Protocol):
    def credential_changes(self) -> AsyncGenerator[None]: ...


class BackendReadinessPort(BackendReadinessObserver, CredentialChangeObserver, Protocol):
    """Probe plus the event that invalidates its cached projection."""


class RuntimeAuthPort(BackendReadinessObserver, BackgroundRefreshStarter, CredentialChangeObserver, Protocol):
    """Combined capability used by the supervisor; no auth implementation import."""


__all__ = [
    "BackendReadinessObservation",
    "BackendReadinessObserver",
    "BackendReadinessPort",
    "BackgroundRefreshStarter",
    "CredentialChangeObserver",
    "RuntimeAuthPort",
]
