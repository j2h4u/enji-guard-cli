"""Product-facing models for the Auth Session bounded context.

The old ``auth`` module remains the compatibility implementation during the
strangler migration.  These aliases deliberately keep its durable on-disk
schema unchanged while giving new callers a context-owned import surface.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from enji_guard_cli.auth_session.payloads import AuthRefreshPayload, AuthStatusPayload
from enji_guard_cli.auth_session.store import (
    BearerTokenCredential,
    CookieCredential,
    Credential,
    CredentialType,
    PendingRefreshRotation,
    StoredAuth,
)


class ImportCredentialPayload(TypedDict):
    ok: bool
    auth_file: str
    credential_type: str
    cookie_count: NotRequired[int]


@dataclass(frozen=True, slots=True)
class AuthSessionStatus:
    """Safe status projection; credential material is intentionally absent."""

    authenticated: bool
    credential_type: str | None
    code: str | None = None
    message: str | None = None
    email: str | None = None
    name: str | None = None
    user_id: str | None = None
    auth_file: Path | None = None

    @classmethod
    def from_payload(cls, payload: AuthStatusPayload) -> AuthSessionStatus:
        return cls(
            authenticated=payload["authenticated"],
            credential_type=payload["credential_type"],
            code=payload["code"],
            message=payload["message"],
            email=payload["email"],
            name=payload["name"],
            user_id=payload["user_id"],
            auth_file=Path(payload["auth_file"]),
        )


@dataclass(frozen=True, slots=True)
class AuthSessionRefreshResult:
    """Safe projection returned after cookie rotation."""

    ok: bool
    credential_type: str
    cookie_count: int = 0
    access_expires_at: datetime | None = None
    auth_file: Path | None = None

    @classmethod
    def from_payload(cls, payload: AuthRefreshPayload) -> AuthSessionRefreshResult:
        expires = payload["access_expires_at"]
        parsed: datetime | None = None
        if expires is not None:
            try:
                parsed = datetime.fromisoformat(expires)
            except ValueError:
                parsed = None
        return cls(
            ok=payload["ok"],
            credential_type=payload["credential_type"],
            cookie_count=payload["cookie_count"],
            access_expires_at=parsed,
            auth_file=Path(payload["auth_file"]),
        )


__all__ = [
    "AuthRefreshPayload",
    "AuthSessionRefreshResult",
    "AuthSessionStatus",
    "AuthStatusPayload",
    "BearerTokenCredential",
    "CookieCredential",
    "Credential",
    "CredentialType",
    "ImportCredentialPayload",
    "PendingRefreshRotation",
    "StoredAuth",
]
