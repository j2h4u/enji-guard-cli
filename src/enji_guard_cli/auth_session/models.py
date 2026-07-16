"""Product-facing models for the Auth Session bounded context.

These models own the durable credential schema and the typed results exposed
to the application and gateway adapter seams.
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
class AuthBackendReadinessResult:
    """Auth-owned result of observing backend access.

    Runtime translates this value into its operational readiness state.  The
    auth context deliberately does not depend on runtime/telemetry modules.
    """

    ready: bool
    failure_kind: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    failure_status_code: int | None = None
    credential_type: str | None = None
    elapsed_ms: int | None = None


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
    "AuthBackendReadinessResult",
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
