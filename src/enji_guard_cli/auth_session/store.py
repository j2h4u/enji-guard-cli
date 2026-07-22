import json
from datetime import UTC, datetime
from enum import StrEnum
from os import O_RDONLY, close, fsync
from os import open as os_open
from pathlib import Path
from typing import Literal, TypedDict, cast

from enji_guard_cli.atomic_json import write_atomic_json


class CredentialType(StrEnum):
    COOKIE = "cookie"
    BEARER_TOKEN = "bearer_token"


class CookieCredential(TypedDict):
    type: Literal["cookie"]
    cookie_header: str


class BearerTokenCredential(TypedDict):
    type: Literal["bearer_token"]
    token: str


Credential = CookieCredential | BearerTokenCredential


class StoredAuth(TypedDict):
    version: int
    base_url: str
    credential: Credential
    imported_at: str


class PendingRefreshRotation(TypedDict):
    version: Literal[2]
    state: Literal["reserved", "requested", "rotated"]
    previous_auth: StoredAuth
    replacement_cookie_header: str | None
    error_type: str | None
    errno: int | None


def stored_auth(base_url: str, credential: Credential) -> StoredAuth:
    return {
        "version": 1,
        "base_url": base_url,
        "credential": credential,
        "imported_at": datetime.now(UTC).isoformat(),
    }


def write_auth_file(path: Path, payload: StoredAuth) -> None:
    write_atomic_json(path, payload, indent=2)


def load_auth_file(path: Path) -> StoredAuth | None:
    try:
        loaded = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    if loaded.get("version") != 1:
        return None
    if not isinstance(loaded.get("base_url"), str):
        return None
    if not isinstance(loaded.get("imported_at"), str):
        return None
    return _load_stored_auth(loaded)


def replace_cookie_credential(path: Path, stored: StoredAuth, cookie_header: str) -> StoredAuth:
    credential = stored["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        raise ValueError("stored credential is not cookie based")
    updated_auth: StoredAuth = {
        "version": stored["version"],
        "base_url": stored["base_url"],
        "credential": {"type": "cookie", "cookie_header": cookie_header},
        "imported_at": stored["imported_at"],
    }
    write_auth_file(path, updated_auth)
    return updated_auth


def pending_rotation_path(auth_path: Path) -> Path:
    return auth_path.with_name(f".{auth_path.name}.rotation.pending")


def reserve_pending_rotation(auth_path: Path, previous_auth: StoredAuth) -> PendingRefreshRotation:
    journal_path = pending_rotation_path(auth_path)
    if journal_path.exists():
        raise FileExistsError(journal_path)
    pending: PendingRefreshRotation = {
        "version": 2,
        "state": "reserved",
        "previous_auth": previous_auth,
        "replacement_cookie_header": None,
        "error_type": None,
        "errno": None,
    }
    _write_json_file(journal_path, pending)
    return pending


def load_pending_rotation(auth_path: Path) -> PendingRefreshRotation | None:
    journal_path = pending_rotation_path(auth_path)
    try:
        loaded = cast(object, json.loads(journal_path.read_text(encoding="utf-8")))
    except OSError, json.JSONDecodeError:
        return None
    return _pending_rotation_from_loaded(loaded)


def _pending_rotation_from_loaded(loaded: object) -> PendingRefreshRotation | None:
    if not isinstance(loaded, dict) or loaded.get("version") not in {1, 2}:
        return None
    version = loaded["version"]
    state = loaded.get("state")
    previous_auth = loaded.get("previous_auth")
    replacement = loaded.get("replacement_cookie_header")
    if state not in {"reserved", "requested", "rotated"} or not isinstance(previous_auth, dict):
        return None
    validated_auth = _load_stored_auth(previous_auth)
    if validated_auth is None or (replacement is not None and not isinstance(replacement, str)):
        return None
    if state == "rotated" and not isinstance(replacement, str):
        return None
    # Version 1 only recorded the reservation before the request.  A process
    # crash could therefore leave an ambiguous journal after the one-time
    # refresh token had already been consumed.  Recover old reservations
    # conservatively as an outcome-unknown request; new version 2 journals
    # have a separate pre-request state for safe cleanup and retry.
    normalized_state = "requested" if version == 1 and state == "reserved" else state
    return {
        "version": 2,
        "state": cast(Literal["reserved", "requested", "rotated"], normalized_state),
        "previous_auth": validated_auth,
        "replacement_cookie_header": replacement,
        "error_type": loaded.get("error_type") if isinstance(loaded.get("error_type"), str) else None,
        "errno": loaded.get("errno") if isinstance(loaded.get("errno"), int) else None,
    }


def mark_pending_rotation_rotated(
    auth_path: Path, pending: PendingRefreshRotation, replacement_cookie_header: str
) -> PendingRefreshRotation:
    updated: PendingRefreshRotation = {
        **pending,
        "state": "rotated",
        "replacement_cookie_header": replacement_cookie_header,
        "error_type": None,
        "errno": None,
    }
    _write_json_file(pending_rotation_path(auth_path), updated)
    return updated


def mark_pending_rotation_requested(auth_path: Path, pending: PendingRefreshRotation) -> PendingRefreshRotation:
    """Durably mark that the non-replayable refresh request is being sent."""
    if pending["state"] != "reserved":
        raise ValueError("pending refresh rotation is not reserved")
    updated: PendingRefreshRotation = {**pending, "state": "requested"}
    _write_json_file(pending_rotation_path(auth_path), updated)
    return updated


def record_pending_rotation_error(
    auth_path: Path, pending: PendingRefreshRotation, error_type: str, errno: int | None
) -> None:
    _write_json_file(
        pending_rotation_path(auth_path),
        {**pending, "error_type": error_type, "errno": errno},
    )


def consume_pending_rotation(auth_path: Path) -> None:
    journal_path = pending_rotation_path(auth_path)
    try:
        journal_path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(journal_path.parent)


def _load_stored_auth(loaded: dict[object, object]) -> StoredAuth | None:
    raw_credential = loaded.get("credential")
    if not isinstance(raw_credential, dict):
        return None
    credential = _load_credential(raw_credential)
    if credential is None:
        return None
    return {
        "version": 1,
        "base_url": cast(str, loaded["base_url"]),
        "credential": credential,
        "imported_at": cast(str, loaded["imported_at"]),
    }


def _load_credential(raw_credential: dict[object, object]) -> Credential | None:
    credential_type = raw_credential.get("type")
    cookie_header = raw_credential.get("cookie_header")
    if credential_type == CredentialType.COOKIE.value and isinstance(cookie_header, str):
        return {"type": "cookie", "cookie_header": cookie_header}
    token = raw_credential.get("token")
    if credential_type == CredentialType.BEARER_TOKEN.value and isinstance(token, str):
        return {"type": "bearer_token", "token": token}
    return None


def _fsync_directory(path: Path) -> None:
    """Best-effort sync for cleanup after the journal may already be absent."""
    try:
        directory_fd = os_open(path, O_RDONLY)
    except OSError:
        return
    try:
        fsync(directory_fd)
    finally:
        close(directory_fd)


def _write_json_file(path: Path, payload: object) -> None:
    write_atomic_json(path, payload, indent=2)
