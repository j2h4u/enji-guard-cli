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
    Ready,
    Rejected,
    Requested,
    Reserved,
    Rotated,
    RotationOutcome,
    RotationState,
    rotation_event_metadata,
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
    successor_revision: str | None
    outcome: RotationOutcome | None
    event_key: str | None
    outbox_enqueued: bool


class OutcomeOutboxRecordPayload(TypedDict):
    outcome: RotationOutcome
    event_key: str


class OutcomeOutboxPayload(TypedDict):
    version: Literal[2]
    records: list[OutcomeOutboxRecordPayload]


JournalStateName = Literal["RESERVED", "REQUESTED", "ROTATED", "REJECTED", "OUTCOME_UNKNOWN"]


@dataclass(frozen=True, slots=True)
class _JournalFields:
    source_revision: str
    raw_state: object
    replacement_cookie_header: str | None
    reason: str | None
    successor_revision: str | None
    outcome: RotationOutcome | None
    event_key: str | None
    outbox_enqueued: bool


@dataclass(frozen=True, slots=True)
class _JournalPayloadFields:
    source_revision: str
    state: JournalStateName
    replacement_cookie_header: str | None
    reason: str | None
    successor_revision: str | None
    outcome: RotationOutcome | None
    event_key: str | None
    outbox_enqueued: bool


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
    outbox_enqueued: bool = False


JournalLoadResult = JournalAbsent | JournalCorrupt | JournalIoFailure | JournalLoaded


@dataclass(frozen=True, slots=True)
class OutcomeOutboxRecord:
    """A non-secret terminal outcome awaiting sink acknowledgement."""

    outcome: RotationOutcome
    event_key: str


@dataclass(frozen=True, slots=True)
class OutcomeOutboxAbsent:
    pass


@dataclass(frozen=True, slots=True)
class OutcomeOutboxCorrupt:
    detail: str


@dataclass(frozen=True, slots=True)
class OutcomeOutboxIoFailure:
    operation: str
    error: OSError


@dataclass(frozen=True, slots=True)
class OutcomeOutboxLoaded:
    records: tuple[OutcomeOutboxRecord, ...]


OutcomeOutboxLoadResult = OutcomeOutboxAbsent | OutcomeOutboxCorrupt | OutcomeOutboxIoFailure | OutcomeOutboxLoaded


@dataclass(frozen=True, slots=True)
class CasWritten:
    auth: StoredAuth


@dataclass(frozen=True, slots=True)
class CasSuperseded:
    current_revision: str | None


CasResult = CasWritten | CasSuperseded


class StorageFailpoint(Protocol):
    def __call__(self, operation: str) -> None: ...


def stored_auth(base_url: str, credential: Credential, *, revision: str | None = None) -> StoredAuth:
    """Create a fresh credential revision, including for identical imports."""

    return {
        "version": AUTH_SCHEMA_VERSION,
        "revision": revision if revision is not None else uuid4().hex,
        "base_url": base_url,
        "credential": credential,
        "imported_at": datetime.now(UTC).isoformat(),
    }


def auth_lock_path(auth_path: Path) -> Path:
    return auth_path.with_suffix(f"{auth_path.suffix}.lock")


def pending_rotation_path(auth_path: Path) -> Path:
    return auth_path.with_name(f".{auth_path.name}.rotation.pending")


def pending_outcome_path(auth_path: Path) -> Path:
    """Return the independent, non-secret terminal-outcome outbox path."""

    return auth_path.with_name(f".{auth_path.name}.rotation.outbox")


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
    """Temporary legacy adapter while runtime readers migrate to typed projections."""

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


def load_outbox(auth_path: Path) -> OutcomeOutboxLoadResult:
    """Load terminal outcomes without coupling them to rotation generation state."""

    path = pending_outcome_path(auth_path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return OutcomeOutboxAbsent()
    except OSError as exc:
        return OutcomeOutboxIoFailure("read outcome outbox", exc)
    try:
        loaded = cast(object, json.loads(raw_text))
    except json.JSONDecodeError as exc:
        return OutcomeOutboxCorrupt(f"invalid JSON: {exc.msg}")
    return _parse_outbox(loaded)


def write_auth_file(path: Path, payload: StoredAuth, *, failpoint: StorageFailpoint | None = None) -> None:
    _trigger(failpoint, "before_write_credential")
    write_atomic_json(path, payload, indent=2, failpoint=failpoint)
    _trigger(failpoint, "after_write_credential")


def write_journal(
    auth_path: Path,
    state: RotationState,
    *,
    outbox_enqueued: bool = False,
    failpoint: StorageFailpoint | None = None,
) -> None:
    payload = _journal_payload(state, outbox_enqueued=outbox_enqueued)
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


def enqueue_outcome(auth_path: Path, record: OutcomeOutboxRecord, *, failpoint: StorageFailpoint | None = None) -> None:
    """Durably append one terminal outcome, retaining prior unacknowledged records."""

    records = _outbox_records_or_raise(load_outbox(auth_path))
    if any(existing.event_key == record.event_key for existing in records):
        return
    _trigger(failpoint, "before_enqueue_outcome")
    write_atomic_json(
        pending_outcome_path(auth_path), _outbox_payload((*records, record)), indent=2, failpoint=failpoint
    )
    _trigger(failpoint, "after_enqueue_outcome")


def acknowledge_outcome(auth_path: Path, event_key: str, *, failpoint: StorageFailpoint | None = None) -> None:
    """Remove an accepted outcome durably; duplicate delivery remains safe on a crash."""

    records = _outbox_records_or_raise(load_outbox(auth_path))
    retained = tuple(record for record in records if record.event_key != event_key)
    if len(retained) == len(records):
        return
    _trigger(failpoint, "before_acknowledge_outcome")
    path = pending_outcome_path(auth_path)
    if retained:
        write_atomic_json(path, _outbox_payload(retained), indent=2, failpoint=failpoint)
    else:
        _trigger(failpoint, "before_unlink_outcome")
        try:
            _trigger(failpoint, "unlink")
            path.unlink()
        except FileNotFoundError:
            return
        fsync_directory(path.parent, failpoint=failpoint)
        _trigger(failpoint, "after_unlink_outcome")
    _trigger(failpoint, "after_acknowledge_outcome")


def cas_replace_cookie(
    auth_path: Path,
    source_revision: str,
    replacement_cookie_header: str,
    *,
    successor_revision: str | None = None,
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
        revision=successor_revision,
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
    fields = _journal_fields(loaded)
    if isinstance(fields, JournalCorrupt):
        return fields
    state = _journal_state(fields)
    return (
        JournalLoaded(state, fields.outbox_enqueued)
        if state is not None
        else JournalCorrupt("journal state payload is inconsistent")
    )


def _parse_outbox(loaded: object) -> OutcomeOutboxLoadResult:
    if not isinstance(loaded, dict) or loaded.get("version") != AUTH_SCHEMA_VERSION:
        return OutcomeOutboxCorrupt("outbox version must be 2")
    raw_records = loaded.get("records")
    if not isinstance(raw_records, list):
        return OutcomeOutboxCorrupt("outbox records must be a list")
    records: list[OutcomeOutboxRecord] = []
    event_keys: set[str] = set()
    for raw_record in raw_records:
        record = _parse_outbox_record(raw_record)
        if record is None:
            return OutcomeOutboxCorrupt("outbox records must contain valid unique outcome event keys")
        if record.event_key in event_keys:
            return OutcomeOutboxCorrupt("outbox event keys must be unique")
        event_keys.add(record.event_key)
        records.append(record)
    return OutcomeOutboxLoaded(tuple(records))


def _parse_outbox_record(raw_record: object) -> OutcomeOutboxRecord | None:
    if not isinstance(raw_record, dict):
        return None
    raw_outcome = raw_record.get("outcome")
    event_key = raw_record.get("event_key")
    if not isinstance(event_key, str) or not event_key:
        return None
    match raw_outcome:
        case "rotated" | "rejected" | "outcome_unknown" as outcome:
            if event_key.startswith("auth-rotation:") and event_key.endswith(f":{outcome}"):
                return OutcomeOutboxRecord(outcome, event_key)
        case _:
            pass
    return None


def _outbox_records_or_raise(loaded: OutcomeOutboxLoadResult) -> tuple[OutcomeOutboxRecord, ...]:
    match loaded:
        case OutcomeOutboxAbsent():
            return ()
        case OutcomeOutboxLoaded(records=records):
            return records
        case OutcomeOutboxCorrupt(detail=detail):
            raise OSError(f"outcome outbox is corrupt: {detail}")
        case OutcomeOutboxIoFailure(operation=operation, error=error):
            raise OSError(f"{operation} failed: {error}") from error
        case _:
            raise TypeError(f"unexpected outbox load result: {type(loaded).__name__}")


def _journal_fields(payload: dict[object, object]) -> _JournalFields | JournalCorrupt:
    source_revision = payload.get("source_revision")
    replacement = payload.get("replacement_cookie_header")
    reason = payload.get("reason")
    successor_revision = payload.get("successor_revision")
    outcome = payload.get("outcome")
    event_key = payload.get("event_key")
    outbox_enqueued = payload.get("outbox_enqueued")
    if not isinstance(source_revision, str) or not source_revision:
        return JournalCorrupt("journal source_revision must be a non-empty string")
    if not isinstance(replacement, str | type(None)) or not isinstance(reason, str | type(None)):
        return JournalCorrupt("journal replacement_cookie_header and reason must be strings or null")
    if not isinstance(successor_revision, str | type(None)) or not isinstance(event_key, str | type(None)):
        return JournalCorrupt("journal successor_revision and event_key must be strings or null")
    if not isinstance(outbox_enqueued, bool):
        return JournalCorrupt("journal outbox_enqueued must be a boolean")
    match outcome:
        case "rotated":
            parsed_outcome: RotationOutcome | None = "rotated"
        case "rejected":
            parsed_outcome = "rejected"
        case "outcome_unknown":
            parsed_outcome = "outcome_unknown"
        case None:
            parsed_outcome = None
        case _:
            return JournalCorrupt("journal outcome must be a known terminal outcome or null")
    return _JournalFields(
        source_revision,
        payload.get("state"),
        replacement,
        reason,
        successor_revision,
        parsed_outcome,
        event_key,
        outbox_enqueued,
    )


def _journal_state(fields: _JournalFields) -> RotationState | None:
    match (
        fields.raw_state,
        fields.replacement_cookie_header,
        fields.reason,
        fields.successor_revision,
        fields.outcome,
        fields.event_key,
        fields.outbox_enqueued,
    ):
        case "RESERVED", None, None, None, None, None, False:
            return Reserved(fields.source_revision)
        case "REQUESTED", None, None, None, None, None, False:
            return Requested(fields.source_revision)
        case "ROTATED", str() as cookie_header, None, str() as successor, "rotated", str() as key, bool():
            state = Rotated(fields.source_revision, cookie_header, successor)
            return state if key == rotation_event_metadata(state).event_key else None
        case "REJECTED", None, str() as rejection_reason, None, "rejected", str() as key, bool():
            state = Rejected(fields.source_revision, rejection_reason)
            return state if key == rotation_event_metadata(state).event_key else None
        case "OUTCOME_UNKNOWN", None, str() as unknown_reason, None, "outcome_unknown", str() as key, bool():
            state = OutcomeUnknown(fields.source_revision, unknown_reason)
            return state if key == rotation_event_metadata(state).event_key else None
    return None


def _journal_payload(state: RotationState, *, outbox_enqueued: bool) -> RotationJournalPayload:
    """Serialize only combinations which represent a valid v2 durable state."""

    match state:
        case Reserved(source_revision=source_revision):
            fields = _JournalPayloadFields(source_revision, "RESERVED", None, None, None, None, None, False)
        case Requested(source_revision=source_revision):
            fields = _JournalPayloadFields(source_revision, "REQUESTED", None, None, None, None, None, False)
        case Rotated(
            source_revision=source_revision, replacement_cookie_header=replacement, successor_revision=successor
        ):
            metadata = rotation_event_metadata(state)
            fields = _JournalPayloadFields(
                source_revision,
                "ROTATED",
                replacement,
                None,
                successor,
                metadata.outcome,
                metadata.event_key,
                outbox_enqueued,
            )
        case Rejected(source_revision=source_revision, reason=reason):
            metadata = rotation_event_metadata(state)
            fields = _JournalPayloadFields(
                source_revision, "REJECTED", None, reason, None, metadata.outcome, metadata.event_key, outbox_enqueued
            )
        case OutcomeUnknown(source_revision=source_revision, reason=reason):
            metadata = rotation_event_metadata(state)
            fields = _JournalPayloadFields(
                source_revision,
                "OUTCOME_UNKNOWN",
                None,
                reason,
                None,
                metadata.outcome,
                metadata.event_key,
                outbox_enqueued,
            )
        case Ready():
            raise TypeError("READY is implicit and cannot be persisted in a rotation journal")
        case _:
            raise TypeError(f"unexpected rotation state: {type(state).__name__}")
    return _journal_payload_base(fields)


def _journal_payload_base(fields: _JournalPayloadFields) -> RotationJournalPayload:
    return {
        "version": AUTH_SCHEMA_VERSION,
        "source_revision": fields.source_revision,
        "state": fields.state,
        "replacement_cookie_header": fields.replacement_cookie_header,
        "reason": fields.reason,
        "successor_revision": fields.successor_revision,
        "outcome": fields.outcome,
        "event_key": fields.event_key,
        "outbox_enqueued": fields.outbox_enqueued,
    }


def _outbox_payload(records: tuple[OutcomeOutboxRecord, ...]) -> OutcomeOutboxPayload:
    return {
        "version": AUTH_SCHEMA_VERSION,
        "records": [{"outcome": record.outcome, "event_key": record.event_key} for record in records],
    }


def _trigger(failpoint: StorageFailpoint | None, operation: str) -> None:
    if failpoint is not None:
        failpoint(operation)
