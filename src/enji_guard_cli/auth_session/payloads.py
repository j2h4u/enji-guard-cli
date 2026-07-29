from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict


class AuthStatusPayload(TypedDict):
    authenticated: bool
    code: str | None
    message: str | None
    auth_file: str
    credential_type: str | None
    refresh_state: str | None
    reauth_required: bool
    email: str | None
    name: str | None
    user_id: str | None


class AuthenticatedProfile(TypedDict):
    email: str | None
    name: str | None
    user_id: str | None


@dataclass(frozen=True, slots=True)
class AuthPayloadRenewal:
    refresh_state: str | None = None
    reauth_required: bool = False
    message: str | None = None


AUTH_RENEWAL_OK = AuthPayloadRenewal()
AUTH_RENEWAL_REAUTH_REQUIRED = AuthPayloadRenewal(reauth_required=True)


class ResponseAdapter(Protocol):
    def json(self, *, operation: str) -> object: ...


def _profile_from_response(response: ResponseAdapter) -> AuthenticatedProfile:
    payload = response.json(operation="auth status")
    if not isinstance(payload, dict):
        return {"email": None, "name": None, "user_id": None}
    return {
        "email": _optional_str(payload.get("email")),
        "name": _optional_str(payload.get("name")),
        "user_id": _optional_str(payload.get("user_id")),
    }


def _authenticated_payload(
    auth_file: Path,
    credential_type: str,
    profile: AuthenticatedProfile,
    *,
    renewal: AuthPayloadRenewal = AUTH_RENEWAL_OK,
) -> AuthStatusPayload:
    return {
        "authenticated": True,
        "code": None,
        "message": renewal.message,
        "auth_file": str(auth_file),
        "credential_type": credential_type,
        "refresh_state": renewal.refresh_state,
        "reauth_required": renewal.reauth_required,
        "email": profile["email"],
        "name": profile["name"],
        "user_id": profile["user_id"],
    }


def _unauthenticated_payload(
    auth_file: Path,
    credential_type: str | None,
    code: str,
    message: str,
    *,
    renewal: AuthPayloadRenewal = AUTH_RENEWAL_REAUTH_REQUIRED,
) -> AuthStatusPayload:
    return {
        "authenticated": False,
        "code": code,
        "message": message,
        "auth_file": str(auth_file),
        "credential_type": credential_type,
        "refresh_state": renewal.refresh_state,
        "reauth_required": renewal.reauth_required,
        "email": None,
        "name": None,
        "user_id": None,
    }


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
