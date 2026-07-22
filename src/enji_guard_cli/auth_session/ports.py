"""Narrow ports consumed by the gateway and runtime."""

from collections.abc import Mapping
from logging import Logger
from pathlib import Path
from typing import Protocol

from enji_guard_cli.auth_session.models import AuthSessionStatus, StoredAuth


class AuthEventSink(Protocol):
    """Narrow callback for safe auth lifecycle events."""

    def __call__(self, logger: Logger, level: int, event: str, fields: Mapping[str, object]) -> None: ...


class AuthOutcomeSink(Protocol):
    """Synchronous durable-outbox delivery boundary for terminal rotations.

    Returning ``True`` means the sink accepted the event durably.  Returning
    ``False`` or raising leaves the terminal journal in place for a later
    delivery attempt.  The event fields contain only the stable ``event_key``.
    """

    def __call__(self, logger: Logger, level: int, event: str, fields: Mapping[str, object]) -> bool: ...


class AuthSessionPort(Protocol):
    """Credential lifecycle operations; no delivery or endpoint controls."""

    def auth_headers(self, stored_auth: StoredAuth) -> dict[str, str]: ...

    def status(self) -> AuthSessionStatus: ...


class AuthStorePort(Protocol):
    def load(self, auth_file: Path) -> StoredAuth | None: ...

    def save(self, auth_file: Path, stored_auth: StoredAuth) -> None: ...


__all__ = [
    "AuthEventSink",
    "AuthOutcomeSink",
    "AuthSessionPort",
    "AuthStorePort",
]
