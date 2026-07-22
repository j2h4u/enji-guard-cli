"""Durable v2 authentication storage.

Files are parsed into typed load results before they enter the state machine.
There is intentionally no v1 reader or migration path: an explicit import is
the safe way to replace malformed or unsupported local state.
"""

import contextlib
import fcntl
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, TypedDict, cast
from uuid import uuid4

from enji_guard_cli.atomic_json import fsync_directory, write_atomic_json
from enji_guard_cli.auth_session.state_machine import (
    OutcomeUnknown,
    Rejected,
    Requested,
    Reserved,
    Rotated,
    RotationState,
)

AUTH_SCHEMA_VERSION = 2
IMPORTED_AT_FUTURE_TOLERANCE = timedelta(seconds=5)


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
    version: Literal[2]
    revision: str
    base_url: str
    credential: Credential
    imported_at: str


class RotationJournalPayload(TypedDict):
    version: Literal[2]
    source_revision: str
    state: Literal["RESERVED", "REQUESTED", "ROTATED", "REJECTED", "OUTCOME_UNKNOWN"]
    replacement_cookie_header: str | None
    reason: str | None


JournalStateName = Literal["RESERVED", "REQUESTED", "ROTATED", "REJECTED", "OUTCOME_UNKNOWN"]


@dataclass(frozen=True, slots=True)
class AuthAbsent:
    pass


@dataclass(frozen=True, slots=True)
class AuthCorrupt:
    detail: str


@dataclass(frozen=True, slots=True)
class AuthUnsupported:
    version: object


@dataclass(frozen=True, slots=True)
class AuthIoFailure:
    operation: str
    error: OSError


@dataclass(frozen=True, slots=True)
class AuthClockAnomaly:
    """A valid credential whose observational timestamp is implausibly future."""

    field: Literal["imported_at"]


@dataclass(frozen=True, slots=True)
class AuthLoaded:
    auth: StoredAuth


AuthLoadResult = AuthAbsent | AuthCorrupt | AuthUnsupported | AuthIoFailure | AuthClockAnomaly | AuthLoaded


@dataclass(frozen=True, slots=True)
class JournalAbsent:
    pass


@dataclass(frozen=True, slots=True)
class JournalCorrupt:
    detail: str


@dataclass(frozen=True, slots=True)
class JournalIoFailure:
    operation: str
    error: OSError


@dataclass(frozen=True, slots=True)
class JournalLoaded:
    state: RotationState


JournalLoadResult = JournalAbsent | JournalCorrupt | JournalIoFailure | JournalLoaded


@dataclass(frozen=True, slots=True)
class CasWritten:
    auth: StoredAuth


@dataclass(frozen=True, slots=True)
class CasSuperseded:
    current_revision: str | None


CasResult = CasWritten | CasSuperseded


class StorageFailpoint(Protocol):
    def __call__(self, operation: str) -> None: ...


def stored_auth(base_url: str, credential: Credential) -> StoredAuth:
    """Create a fresh credential revision, including for identical imports."""

    return {
        "version": AUTH_SCHEMA_VERSION,
        "revision": uuid4().hex,
        "base_url": base_url,
        "credential": credential,
        "imported_at": datetime.now(UTC).isoformat(),
    }


def auth_lock_path(auth_path: Path) -> Path:
    return auth_path.with_suffix(f"{auth_path.suffix}.lock")


def pending_rotation_path(auth_path: Path) -> Path:
    return auth_path.with_name(f".{auth_path.name}.rotation.pending")


@contextlib.contextmanager
def auth_file_lock(auth_path: Path, *, failpoint: StorageFailpoint | None = None):
    """Acquire the POSIX host lock for a short filesystem-only transaction."""

    auth_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = auth_lock_path(auth_path)
    _trigger(failpoint, "lock_open")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        lock_path.chmod(0o600)
        _trigger(failpoint, "lock")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            _trigger(failpoint, "unlock")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_auth(path: Path, *, now: datetime | None = None) -> AuthLoadResult:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return AuthAbsent()
    except OSError as exc:
        return AuthIoFailure("read credential", exc)
    try:
        loaded = cast(object, json.loads(raw_text))
    except json.JSONDecodeError as exc:
        return AuthCorrupt(f"invalid JSON: {exc.msg}")
    return _parse_auth(loaded, now=now)


def load_auth_file(path: Path) -> StoredAuth | None:
    """Return an observer projection; mutation code uses ``load_auth``."""

    result = load_auth(path)
    return result.auth if isinstance(result, AuthLoaded) else None


def load_journal(auth_path: Path) -> JournalLoadResult:
    path = pending_rotation_path(auth_path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return JournalAbsent()
    except OSError as exc:
        return JournalIoFailure("read refresh journal", exc)
    try:
        loaded = cast(object, json.loads(raw_text))
    except json.JSONDecodeError as exc:
        return JournalCorrupt(f"invalid JSON: {exc.msg}")
    return _parse_journal(loaded)


def write_auth_file(path: Path, payload: StoredAuth, *, failpoint: StorageFailpoint | None = None) -> None:
    _trigger(failpoint, "before_write_credential")
    write_atomic_json(path, payload, indent=2, failpoint=failpoint)
    _trigger(failpoint, "after_write_credential")


def write_journal(auth_path: Path, state: RotationState, *, failpoint: StorageFailpoint | None = None) -> None:
    if isinstance(state, Rotated):
        replacement_cookie_header: str | None = state.replacement_cookie_header
        reason: str | None = None
        persisted_state: JournalStateName = "ROTATED"
        source_revision = state.source_revision
    elif isinstance(state, Reserved):
        replacement_cookie_header = None
        reason = None
        persisted_state = "RESERVED"
        source_revision = state.source_revision
    elif isinstance(state, Requested):
        replacement_cookie_header = None
        reason = None
        persisted_state = "REQUESTED"
        source_revision = state.source_revision
    elif isinstance(state, Rejected):
        replacement_cookie_header = None
        reason = state.reason
        persisted_state = "REJECTED"
        source_revision = state.source_revision
    elif isinstance(state, OutcomeUnknown):
        replacement_cookie_header = None
        reason = state.reason
        persisted_state = "OUTCOME_UNKNOWN"
        source_revision = state.source_revision
    else:
        raise TypeError(f"journal state must be a rotation state, got {type(state).__name__}")
    payload: RotationJournalPayload = {
        "version": AUTH_SCHEMA_VERSION,
        "source_revision": source_revision,
        "state": persisted_state,
        "replacement_cookie_header": replacement_cookie_header,
        "reason": reason,
    }
    _trigger(failpoint, "before_write_journal")
    write_atomic_json(pending_rotation_path(auth_path), payload, indent=2, failpoint=failpoint)
    _trigger(failpoint, "after_write_journal")


def delete_journal(auth_path: Path, *, failpoint: StorageFailpoint | None = None) -> None:
    path = pending_rotation_path(auth_path)
    _trigger(failpoint, "before_delete_journal")
    try:
        _trigger(failpoint, "unlink")
        path.unlink()
    except FileNotFoundError:
        return
    fsync_directory(path.parent, failpoint=failpoint)
    _trigger(failpoint, "after_delete_journal")


def cas_replace_cookie(
    auth_path: Path,
    source_revision: str,
    replacement_cookie_header: str,
    *,
    failpoint: StorageFailpoint | None = None,
) -> CasResult:
    """CAS-write a rotated cookie while the caller holds ``auth_file_lock``."""

    loaded = load_auth(auth_path)
    if not isinstance(loaded, AuthLoaded):
        return CasSuperseded(None)
    current = loaded.auth
    if current["revision"] != source_revision:
        return CasSuperseded(current["revision"])
    credential = current["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        return CasSuperseded(current["revision"])
    replacement = stored_auth(
        current["base_url"],
        {"type": CredentialType.COOKIE.value, "cookie_header": replacement_cookie_header},
    )
    write_auth_file(auth_path, replacement, failpoint=failpoint)
    return CasWritten(replacement)


def _parse_auth(loaded: object, *, now: datetime | None = None) -> AuthLoadResult:
    if not isinstance(loaded, dict):
        return AuthCorrupt("credential payload must be an object")
    version = loaded.get("version")
    if version != AUTH_SCHEMA_VERSION:
        return AuthUnsupported(version)
    metadata = _credential_metadata(loaded)
    if metadata is None:
        return AuthCorrupt("credential revision, base_url, and imported_at must be non-empty strings")
    revision, base_url, imported_at = metadata
    imported_at_validation = _validate_imported_at(imported_at, now=now)
    if not isinstance(imported_at_validation, datetime):
        return imported_at_validation
    raw_credential = loaded.get("credential")
    credential = _parse_credential(raw_credential)
    if credential is None:
        return AuthCorrupt("credential is invalid")
    return AuthLoaded(
        {
            "version": AUTH_SCHEMA_VERSION,
            "revision": revision,
            "base_url": base_url,
            "credential": credential,
            "imported_at": imported_at,
        }
    )


def _credential_metadata(payload: Mapping[object, object]) -> tuple[str, str, str] | None:
    """Return the required non-empty identity fields in storage order."""

    match payload.get("revision"), payload.get("base_url"), payload.get("imported_at"):
        case str() as revision, str() as base_url, str() as imported_at if revision and base_url and imported_at:
            return revision, base_url, imported_at
        case _:
            return None


def _parse_utc_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(UTC)


def _validate_imported_at(value: str, *, now: datetime | None) -> datetime | AuthCorrupt | AuthClockAnomaly:
    parsed = _parse_utc_timestamp(value)
    if parsed is None:
        return AuthCorrupt("credential imported_at must be an ISO 8601 UTC timestamp")
    current_time = (now if now is not None else datetime.now(UTC)).astimezone(UTC)
    if parsed > current_time + IMPORTED_AT_FUTURE_TOLERANCE:
        return AuthClockAnomaly("imported_at")
    return parsed


def _parse_credential(raw: object) -> Credential | None:
    if not isinstance(raw, dict):
        return None
    credential_type = raw.get("type")
    if credential_type == CredentialType.COOKIE.value and isinstance(raw.get("cookie_header"), str):
        return {"type": "cookie", "cookie_header": cast(str, raw["cookie_header"])}
    if credential_type == CredentialType.BEARER_TOKEN.value and isinstance(raw.get("token"), str):
        return {"type": "bearer_token", "token": cast(str, raw["token"])}
    return None


def _parse_journal(loaded: object) -> JournalLoadResult:
    if not isinstance(loaded, dict) or loaded.get("version") != AUTH_SCHEMA_VERSION:
        return JournalCorrupt("journal version must be 2")
    source_revision = loaded.get("source_revision")
    raw_state = loaded.get("state")
    replacement = loaded.get("replacement_cookie_header")
    reason = loaded.get("reason")
    if not isinstance(source_revision, str) or not source_revision:
        return JournalCorrupt("journal source_revision must be a non-empty string")
    if replacement is not None and not isinstance(replacement, str):
        return JournalCorrupt("journal replacement_cookie_header must be a string or null")
    if reason is not None and not isinstance(reason, str):
        return JournalCorrupt("journal reason must be a string or null")
    state = _journal_state(source_revision, raw_state, replacement, reason)
    return JournalLoaded(state) if state is not None else JournalCorrupt("journal state payload is inconsistent")


def _journal_state(
    source_revision: str, raw_state: object, replacement: object, reason: object
) -> RotationState | None:
    match raw_state, replacement, reason:
        case "RESERVED", None, None:
            return Reserved(source_revision)
        case "REQUESTED", None, None:
            return Requested(source_revision)
        case "ROTATED", str() as cookie_header, None:
            return Rotated(source_revision, cookie_header)
        case "REJECTED", None, str() as rejection_reason:
            return Rejected(source_revision, rejection_reason)
        case "OUTCOME_UNKNOWN", None, str() as unknown_reason:
            return OutcomeUnknown(source_revision, unknown_reason)
        case _:
            return None


def _trigger(failpoint: StorageFailpoint | None, operation: str) -> None:
    if failpoint is not None:
        failpoint(operation)
