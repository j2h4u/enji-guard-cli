from pathlib import Path
from typing import Protocol, TypedDict

from enji_guard_cli.auth_impl.cookies import cookie_count, cookie_value, jwt_expires_at
from enji_guard_cli.auth_impl.store import CredentialType, StoredAuth


class AuthStatusPayload(TypedDict):
    authenticated: bool
    code: str | None
    message: str | None
    auth_file: str
    credential_type: str | None
    email: str | None
    name: str | None
    user_id: str | None


class AuthRefreshPayload(TypedDict):
    ok: bool
    auth_file: str
    credential_type: str
    cookie_count: int
    access_expires_at: str | None


class AuthenticatedProfile(TypedDict):
    email: str | None
    name: str | None
    user_id: str | None


class ResponseAdapter(Protocol):
    def json(self, *, operation: str) -> object: ...


def _auth_refresh_payload(auth_file: Path, stored_auth: StoredAuth) -> AuthRefreshPayload:
    credential = stored_auth["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        raise ValueError("stored credential is not cookie based")
    access_token = cookie_value(credential["cookie_header"], "access_token")
    expires_at = jwt_expires_at(access_token) if access_token is not None else None
    return {
        "ok": True,
        "auth_file": str(auth_file),
        "credential_type": CredentialType.COOKIE.value,
        "cookie_count": cookie_count(credential["cookie_header"]),
        "access_expires_at": expires_at.isoformat() if expires_at is not None else None,
    }


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
) -> AuthStatusPayload:
    return {
        "authenticated": True,
        "code": None,
        "message": None,
        "auth_file": str(auth_file),
        "credential_type": credential_type,
        "email": profile["email"],
        "name": profile["name"],
        "user_id": profile["user_id"],
    }


def _unauthenticated_payload(
    auth_file: Path,
    credential_type: str | None,
    code: str,
    message: str,
) -> AuthStatusPayload:
    return {
        "authenticated": False,
        "code": code,
        "message": message,
        "auth_file": str(auth_file),
        "credential_type": credential_type,
        "email": None,
        "name": None,
        "user_id": None,
    }


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
