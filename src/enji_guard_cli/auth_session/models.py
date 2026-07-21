"""Product-facing models for the Auth Session bounded context.

These models own the durable credential schema and the typed results exposed
to the application and gateway adapter seams.
"""

from dataclasses import dataclass
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


__all__ = [
    "AuthBackendReadinessResult",
    "AuthRefreshPayload",
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
