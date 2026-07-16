"""Narrow ports consumed by the gateway and runtime."""

from pathlib import Path
from typing import Protocol

from enji_guard_cli.auth_session.models import AuthSessionRefreshResult, AuthSessionStatus, StoredAuth


class AuthSessionPort(Protocol):
    """Credential lifecycle operations; no delivery or endpoint controls."""

    def auth_headers(self, stored_auth: StoredAuth) -> dict[str, str]: ...

    def status(self) -> AuthSessionStatus: ...

    def refresh(self) -> AuthSessionRefreshResult: ...


class AuthStorePort(Protocol):
    def load(self, auth_file: Path) -> StoredAuth | None: ...

    def save(self, auth_file: Path, stored_auth: StoredAuth) -> None: ...


__all__ = ["AuthSessionPort", "AuthStorePort"]
