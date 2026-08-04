"""Durable authentication storage with a v3 refresh journal.

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
    AMBIGUITY_REPLAY_LIMIT,
    SAFE_RETRY_LIMIT,
    TOTAL_DISPATCH_CAP,
    VALIDATION_RETRY_LIMIT,
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
JOURNAL_SCHEMA_VERSION = 3
PREVIOUS_JOURNAL_SCHEMA_VERSION = 2
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
    version: Literal[3]
    source_revision: str
    state: Literal["RESERVED", "REQUESTED", "ROTATED", "REJECTED", "OUTCOME_UNKNOWN"]
    replacement_cookie_header: str | None
    reason: str | None
    successor_revision: str | None
    outcome: RotationOutcome | None
    event_key: str | None
    outbox_enqueued: bool
    rotation_id: str
    attempt_kind: Literal["normal", "safe_retry", "validation_recovery", "ambiguity_replay"]
    dispatch_count: int
    safe_retry_count: int
    validation_retry_count: int
    ambiguity_replay_count: int
    recovery_deadline: str | None
    continuation: Literal["safe_retry", "validation_recovery", "ambiguity_replay", "stop"] | None
    owner_boot_id: str | None
    owner_pid: int | None
    owner_start_ticks: str | None
    next_attempt_at: str | None
    total_dispatch_cap: int
    response_class: str | None
    stop_reason: str | None


class OutcomeOutboxRecordPayload(TypedDict):
    outcome: RotationOutcome
    event_key: str


class OutcomeOutboxPayload(TypedDict):
    version: Literal[2]
    records: list[OutcomeOutboxRecordPayload]


JournalStateName = Literal["RESERVED", "REQUESTED", "ROTATED", "REJECTED", "OUTCOME_UNKNOWN"]
AttemptKind = Literal["normal", "safe_retry", "validation_recovery", "ambiguity_replay"]
Continuation = Literal["safe_retry", "validation_recovery", "ambiguity_replay", "stop"]


@dataclass(frozen=True, slots=True)
class RotationAttempt:
    """Durable recovery budget for one locally coordinated credential revision."""

    rotation_id: str
    attempt_kind: AttemptKind
    dispatch_count: int
    safe_retry_count: int = 0
    validation_retry_count: int = 0
    ambiguity_replay_count: int = 0
    recovery_deadline: str | None = None
    continuation: Continuation | None = None
    owner_boot_id: str | None = None
    owner_pid: int | None = None
    owner_start_ticks: str | None = None
    next_attempt_at: str | None = None
    total_dispatch_cap: int = TOTAL_DISPATCH_CAP
    response_class: str | None = None
    stop_reason: str | None = None


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
    attempt: RotationAttempt


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
    attempt: RotationAttempt


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
    attempt: RotationAttempt | None = None
    migrated_from_version: int | None = None


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
    except UnicodeDecodeError:
        return AuthCorrupt("credential file is not valid UTF-8")
    try:
        loaded = cast(object, json.loads(raw_text))
    except json.JSONDecodeError as exc:
        return AuthCorrupt(f"invalid JSON: {exc.msg}")
    return _parse_auth(loaded, now=now)


def load_journal(auth_path: Path) -> JournalLoadResult:
    path = pending_rotation_path(auth_path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return JournalAbsent()
    except OSError as exc:
        return JournalIoFailure("read refresh journal", exc)
    except UnicodeDecodeError:
        return JournalCorrupt("refresh journal file is not valid UTF-8")
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
    except UnicodeDecodeError:
        return OutcomeOutboxCorrupt("outcome outbox file is not valid UTF-8")
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
    attempt: RotationAttempt | None = None,
    failpoint: StorageFailpoint | None = None,
) -> None:
    payload = _journal_payload(state, outbox_enqueued=outbox_enqueued, attempt=attempt)
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


def _as_object_mapping(value: object) -> Mapping[object, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[object, object], value)


def _parse_journal(loaded: object) -> JournalLoadResult:
    payload = _as_object_mapping(loaded)
    if payload is None:
        return JournalCorrupt("journal payload must be an object")
    if payload.get("version") == PREVIOUS_JOURNAL_SCHEMA_VERSION:
        return _parse_v2_journal(payload)
    if payload.get("version") != JOURNAL_SCHEMA_VERSION:
        return JournalCorrupt("journal version must be 2 or 3")
    fields = _journal_fields(payload)
    if isinstance(fields, JournalCorrupt):
        return fields
    state = _journal_state(fields)
    return (
        JournalLoaded(state, fields.outbox_enqueued, fields.attempt)
        if state is not None
        else JournalCorrupt("journal state payload is inconsistent")
    )


def _parse_v2_journal(payload: Mapping[object, object]) -> JournalLoadResult:
    """Conservatively import the last public journal schema.

    A v2 in-flight request has no owner identity or durable timing budget, so
    it is migrated to a stopped unknown outcome. It is never silently treated
    as a dispatchable request under the v3 policy.
    """

    source_revision = payload.get("source_revision")
    raw_state = payload.get("state")
    if not isinstance(source_revision, str) or not source_revision:
        return JournalCorrupt("v2 journal source_revision must be a non-empty string")
    outbox_enqueued = payload.get("outbox_enqueued", False)
    if not isinstance(outbox_enqueued, bool):
        return JournalCorrupt("v2 journal outbox_enqueued must be a boolean")
    if raw_state in {"RESERVED", "REQUESTED"} and any(
        payload.get(field) is not None
        for field in ("replacement_cookie_header", "reason", "successor_revision", "outcome", "event_key")
    ):
        return JournalCorrupt("v2 active journal contains terminal fields")
    attempt = RotationAttempt(
        source_revision,
        "normal",
        1 if raw_state == "REQUESTED" else 0,
        total_dispatch_cap=1 if raw_state == "REQUESTED" else 6,
        continuation="stop" if raw_state in {"REQUESTED", "OUTCOME_UNKNOWN"} else None,
        stop_reason="v2-active-ambiguity" if raw_state in {"REQUESTED", "OUTCOME_UNKNOWN"} else None,
    )
    state = _parse_v2_state(payload, source_revision, raw_state)
    if isinstance(state, JournalCorrupt):
        return state
    return JournalLoaded(state, outbox_enqueued, attempt, migrated_from_version=2)


def _parse_v2_state(
    payload: Mapping[object, object], source_revision: str, raw_state: object
) -> RotationState | JournalCorrupt:
    result: RotationState | JournalCorrupt
    match raw_state:
        case "RESERVED":
            result = Reserved(source_revision)
        case "REQUESTED":
            result = OutcomeUnknown(source_revision, "v2 in-flight refresh outcome is unknown")
        case "ROTATED":
            replacement = payload.get("replacement_cookie_header")
            successor = payload.get("successor_revision")
            if isinstance(replacement, str) and isinstance(successor, str):
                result = Rotated(source_revision, replacement, successor)
            else:
                result = JournalCorrupt("v2 rotated journal is incomplete")
        case "REJECTED" | "OUTCOME_UNKNOWN":
            reason = payload.get("reason")
            if not isinstance(reason, str):
                result = JournalCorrupt("v2 terminal journal is incomplete")
            elif raw_state == "REJECTED":
                result = Rejected(source_revision, reason)
            else:
                result = OutcomeUnknown(source_revision, reason)
        case _:
            result = JournalCorrupt("v2 journal state is unsupported")
    return result


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
    payload = _as_object_mapping(raw_record)
    if payload is None:
        return None
    raw_outcome = payload.get("outcome")
    event_key = payload.get("event_key")
    if not isinstance(event_key, str) or not event_key:
        return None
    if raw_outcome not in {"rotated", "rejected", "outcome_unknown"}:
        return None
    outcome = cast(RotationOutcome, raw_outcome)
    if event_key.startswith("auth-rotation:") and event_key.endswith(f":{outcome}"):
        return OutcomeOutboxRecord(outcome, event_key)
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


def _journal_fields(payload: Mapping[object, object]) -> _JournalFields | JournalCorrupt:
    source_revision = payload.get("source_revision")
    replacement = payload.get("replacement_cookie_header")
    reason = payload.get("reason")
    successor_revision = payload.get("successor_revision")
    outcome = payload.get("outcome")
    event_key = payload.get("event_key")
    outbox_enqueued = payload.get("outbox_enqueued")
    attempt = _parse_rotation_attempt(payload)
    validation_error = _journal_field_error(payload, attempt)
    if validation_error is not None:
        return JournalCorrupt(validation_error)
    parsed_outcome = _parse_journal_outcome(outcome)
    if parsed_outcome is _INVALID_OUTCOME:
        return JournalCorrupt("journal outcome must be a known terminal outcome or null")
    assert isinstance(source_revision, str)
    assert isinstance(replacement, str | type(None))
    assert isinstance(reason, str | type(None))
    assert isinstance(successor_revision, str | type(None))
    assert isinstance(event_key, str | type(None))
    assert isinstance(outbox_enqueued, bool)
    assert isinstance(attempt, RotationAttempt)
    return _JournalFields(
        source_revision,
        payload.get("state"),
        replacement,
        reason,
        successor_revision,
        cast(RotationOutcome | None, parsed_outcome),
        event_key,
        outbox_enqueued,
        attempt,
    )


_INVALID_OUTCOME = object()


def _journal_field_error(payload: Mapping[object, object], attempt: RotationAttempt | None) -> str | None:
    scalar_error = _journal_scalar_field_error(payload)
    if scalar_error is not None:
        return scalar_error
    if attempt is None:
        return "journal recovery budget is invalid"
    return _attempt_state_error(payload.get("state"), attempt)


def _journal_scalar_field_error(payload: Mapping[object, object]) -> str | None:
    source_revision = payload.get("source_revision")
    replacement = payload.get("replacement_cookie_header")
    reason = payload.get("reason")
    successor_revision = payload.get("successor_revision")
    event_key = payload.get("event_key")
    outbox_enqueued = payload.get("outbox_enqueued")
    if not isinstance(source_revision, str) or not source_revision:
        return "journal source_revision must be a non-empty string"
    if not isinstance(replacement, str | type(None)) or not isinstance(reason, str | type(None)):
        return "journal replacement_cookie_header and reason must be strings or null"
    if not isinstance(successor_revision, str | type(None)) or not isinstance(event_key, str | type(None)):
        return "journal successor_revision and event_key must be strings or null"
    if not isinstance(outbox_enqueued, bool):
        return "journal outbox_enqueued must be a boolean"
    return None


def _attempt_state_error(raw_state: object, attempt: RotationAttempt) -> str | None:
    owner_present = attempt.owner_boot_id is not None
    scheduled = attempt.continuation in {"safe_retry", "validation_recovery", "ambiguity_replay"}
    if owner_present and raw_state not in {"RESERVED", "REQUESTED"}:
        return "terminal journal cannot retain a dispatch owner"
    if scheduled and (
        raw_state != "OUTCOME_UNKNOWN"
        or attempt.next_attempt_at is None
        or attempt.recovery_deadline is None
        or attempt.stop_reason is not None
    ):
        return "scheduled recovery metadata is inconsistent"
    if scheduled and not _valid_recovery_window(attempt.next_attempt_at, attempt.recovery_deadline):
        return "scheduled recovery timestamps are invalid"
    if attempt.continuation == "stop" and (
        raw_state not in {"OUTCOME_UNKNOWN", "REJECTED"}
        or attempt.next_attempt_at is not None
        or not attempt.stop_reason
        or owner_present
    ):
        return "stopped recovery metadata is inconsistent"
    if attempt.continuation is None and (attempt.next_attempt_at is not None or attempt.stop_reason is not None):
        return "inactive recovery metadata is inconsistent"
    return None


def _valid_recovery_window(not_before: str | None, deadline: str | None) -> bool:
    if not_before is None or deadline is None:
        return False
    try:
        parsed_not_before = datetime.fromisoformat(not_before)
        parsed_deadline = datetime.fromisoformat(deadline)
    except ValueError:
        return False
    if parsed_not_before.tzinfo is None or parsed_deadline.tzinfo is None:
        return False
    return parsed_not_before.astimezone(UTC) <= parsed_deadline.astimezone(UTC)


def _parse_journal_outcome(value: object) -> RotationOutcome | object | None:
    match value:
        case "rotated" | "rejected" | "outcome_unknown" as outcome:
            return outcome
        case None:
            return None
        case _:
            return _INVALID_OUTCOME


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


def _journal_payload(
    state: RotationState, *, outbox_enqueued: bool, attempt: RotationAttempt | None
) -> RotationJournalPayload:
    """Serialize only combinations which represent a valid v2 durable state."""

    resolved_attempt = attempt if attempt is not None else _initial_rotation_attempt(state)
    match state:
        case Reserved(source_revision=source_revision):
            fields = _JournalPayloadFields(
                source_revision, "RESERVED", None, None, None, None, None, False, resolved_attempt
            )
        case Requested(source_revision=source_revision):
            fields = _JournalPayloadFields(
                source_revision, "REQUESTED", None, None, None, None, None, False, resolved_attempt
            )
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
                resolved_attempt,
            )
        case Rejected(source_revision=source_revision, reason=reason):
            metadata = rotation_event_metadata(state)
            fields = _JournalPayloadFields(
                source_revision,
                "REJECTED",
                None,
                reason,
                None,
                metadata.outcome,
                metadata.event_key,
                outbox_enqueued,
                resolved_attempt,
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
                resolved_attempt,
            )
        case Ready():
            raise TypeError("READY is implicit and cannot be persisted in a rotation journal")
        case _:
            raise TypeError(f"unexpected rotation state: {type(state).__name__}")
    return _journal_payload_base(fields)


def _initial_rotation_attempt(state: RotationState) -> RotationAttempt:
    source_revision = _rotation_source_revision(state)
    if isinstance(state, (Rejected, OutcomeUnknown)):
        return RotationAttempt(
            source_revision,
            "normal",
            0,
            continuation="stop",
            stop_reason="terminal-state",
        )
    return RotationAttempt(source_revision, "normal", 0)


def _parse_rotation_attempt(payload: Mapping[object, object]) -> RotationAttempt | None:
    counts = _parse_attempt_counts(payload)
    recovery = _parse_attempt_recovery(payload)
    if counts is None or recovery is None:
        return None
    attempt = RotationAttempt(
        counts[0],
        counts[1],
        counts[2],
        counts[3],
        counts[4],
        counts[5],
        recovery[0],
        recovery[1],
        recovery[2],
        recovery[3],
        recovery[4],
        recovery[5],
        recovery[6],
        recovery[7],
        recovery[8],
    )
    return attempt if _rotation_attempt_is_valid(attempt) else None


def _parse_attempt_counts(payload: Mapping[object, object]) -> tuple[str, AttemptKind, int, int, int, int] | None:
    values = (
        payload.get("rotation_id"),
        payload.get("attempt_kind"),
        payload.get("dispatch_count"),
        payload.get("safe_retry_count"),
        payload.get("validation_retry_count"),
        payload.get("ambiguity_replay_count"),
    )
    if any(isinstance(value, bool) for value in values):
        return None
    match values:
        case (
            str() as rotation_id,
            "normal" | "safe_retry" | "validation_recovery" | "ambiguity_replay" as attempt_kind,
            int() as dispatch_count,
            int() as safe_retry_count,
            int() as validation_retry_count,
            int() as ambiguity_replay_count,
        ):
            return (
                rotation_id,
                cast(AttemptKind, attempt_kind),
                dispatch_count,
                safe_retry_count,
                validation_retry_count,
                ambiguity_replay_count,
            )
    return None


def _parse_attempt_recovery(
    payload: Mapping[object, object],
) -> (
    tuple[str | None, Continuation | None, str | None, int | None, str | None, str | None, int, str | None, str | None]
    | None
):
    values = (
        payload.get("recovery_deadline"),
        payload.get("continuation"),
        payload.get("owner_boot_id"),
        payload.get("owner_pid"),
        payload.get("owner_start_ticks"),
        payload.get("next_attempt_at"),
        payload.get("total_dispatch_cap"),
        payload.get("response_class"),
        payload.get("stop_reason"),
    )
    if any(isinstance(value, bool) for value in values):
        return None
    match values:
        case (
            str() | None as recovery_deadline,
            "safe_retry" | "validation_recovery" | "ambiguity_replay" | "stop" | None as continuation,
            str() | None as owner_boot_id,
            int() | None as owner_pid,
            str() | None as owner_start_ticks,
            str() | None as next_attempt_at,
            int() as total_dispatch_cap,
            str() | None as response_class,
            str() | None as stop_reason,
        ):
            return (
                recovery_deadline,
                cast(Continuation | None, continuation),
                owner_boot_id,
                owner_pid,
                owner_start_ticks,
                next_attempt_at,
                total_dispatch_cap,
                response_class,
                stop_reason,
            )
    return None


def _rotation_attempt_is_valid(attempt: RotationAttempt) -> bool:
    retry_count = attempt.safe_retry_count + attempt.validation_retry_count + attempt.ambiguity_replay_count
    owner_absent = attempt.owner_boot_id is None and attempt.owner_pid is None and attempt.owner_start_ticks is None
    owner_valid = (
        bool(attempt.owner_boot_id)
        and attempt.owner_pid is not None
        and attempt.owner_pid > 0
        and bool(attempt.owner_start_ticks)
    )
    return (
        bool(attempt.rotation_id)
        and attempt.dispatch_count >= 0
        and 0 <= attempt.safe_retry_count <= SAFE_RETRY_LIMIT
        and 0 <= attempt.validation_retry_count <= VALIDATION_RETRY_LIMIT
        and 0 <= attempt.ambiguity_replay_count <= AMBIGUITY_REPLAY_LIMIT
        and attempt.total_dispatch_cap == TOTAL_DISPATCH_CAP
        and attempt.dispatch_count <= attempt.total_dispatch_cap
        and attempt.dispatch_count in {retry_count, 1 + retry_count}
        and (owner_absent or owner_valid)
    )


def _rotation_source_revision(state: RotationState) -> str:
    if isinstance(state, Ready):
        raise TypeError("READY is implicit and cannot be persisted in a rotation journal")
    return state.source_revision


def _journal_payload_base(fields: _JournalPayloadFields) -> RotationJournalPayload:
    return {
        "version": JOURNAL_SCHEMA_VERSION,
        "source_revision": fields.source_revision,
        "state": fields.state,
        "replacement_cookie_header": fields.replacement_cookie_header,
        "reason": fields.reason,
        "successor_revision": fields.successor_revision,
        "outcome": fields.outcome,
        "event_key": fields.event_key,
        "outbox_enqueued": fields.outbox_enqueued,
        "rotation_id": fields.attempt.rotation_id,
        "attempt_kind": fields.attempt.attempt_kind,
        "dispatch_count": fields.attempt.dispatch_count,
        "safe_retry_count": fields.attempt.safe_retry_count,
        "validation_retry_count": fields.attempt.validation_retry_count,
        "ambiguity_replay_count": fields.attempt.ambiguity_replay_count,
        "recovery_deadline": fields.attempt.recovery_deadline,
        "continuation": fields.attempt.continuation,
        "owner_boot_id": fields.attempt.owner_boot_id,
        "owner_pid": fields.attempt.owner_pid,
        "owner_start_ticks": fields.attempt.owner_start_ticks,
        "next_attempt_at": fields.attempt.next_attempt_at,
        "total_dispatch_cap": fields.attempt.total_dispatch_cap,
        "response_class": fields.attempt.response_class,
        "stop_reason": fields.attempt.stop_reason,
    }


def _outbox_payload(records: tuple[OutcomeOutboxRecord, ...]) -> OutcomeOutboxPayload:
    return {
        "version": AUTH_SCHEMA_VERSION,
        "records": [{"outcome": record.outcome, "event_key": record.event_key} for record in records],
    }


def _trigger(failpoint: StorageFailpoint | None, operation: str) -> None:
    if failpoint is not None:
        failpoint(operation)
