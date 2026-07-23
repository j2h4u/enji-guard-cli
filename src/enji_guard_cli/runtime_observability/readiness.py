import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from enji_guard_cli.atomic_json import write_atomic_json
from enji_guard_cli.settings import ReadinessSettings, default_settings

# Small allowance for producer/consumer wall-clock skew.  A state farther ahead
# is not fresh: its age cannot be trusted until the clock anomaly is resolved.
READINESS_CLOCK_ANOMALY_TOLERANCE = timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class BackendReadinessProbe:
    ready: bool
    failure_kind: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    failure_status_code: int | None = None
    credential_type: str | None = None
    elapsed_ms: int | None = None
    bypass_grace: bool = False


@dataclass(frozen=True, slots=True)
class BackendReadinessState:
    ready: bool | None
    checked_at: str | None
    last_success_at: str | None
    failure_kind: str | None
    failure_code: str | None
    failure_message: str | None
    failure_status_code: int | None
    credential_type: str | None
    consecutive_failures: int
    bypass_grace: bool = False


@dataclass(frozen=True, slots=True)
class ReadinessVerdict:
    ready: bool
    reason: str | None
    state: BackendReadinessState | None


INITIAL_BACKEND_READINESS_STATE = BackendReadinessState(
    ready=None,
    checked_at=None,
    last_success_at=None,
    failure_kind=None,
    failure_code=None,
    failure_message=None,
    failure_status_code=None,
    credential_type=None,
    consecutive_failures=0,
)


def backend_readiness_state_after_probe(
    previous: BackendReadinessState,
    probe: BackendReadinessProbe,
    *,
    checked_at: datetime,
) -> BackendReadinessState:
    checked_at_text = checked_at.astimezone(UTC).isoformat()
    if probe.ready:
        return BackendReadinessState(
            ready=True,
            checked_at=checked_at_text,
            last_success_at=checked_at_text,
            failure_kind=None,
            failure_code=None,
            failure_message=None,
            failure_status_code=None,
            credential_type=probe.credential_type,
            consecutive_failures=0,
            bypass_grace=False,
        )
    return BackendReadinessState(
        ready=False,
        checked_at=checked_at_text,
        last_success_at=previous.last_success_at,
        failure_kind=probe.failure_kind,
        failure_code=probe.failure_code,
        failure_message=probe.failure_message,
        failure_status_code=probe.failure_status_code,
        credential_type=probe.credential_type,
        consecutive_failures=previous.consecutive_failures + 1,
        bypass_grace=probe.bypass_grace,
    )


def backend_readiness_starting_state(*, checked_at: datetime) -> BackendReadinessState:
    return BackendReadinessState(
        ready=False,
        checked_at=checked_at.astimezone(UTC).isoformat(),
        last_success_at=None,
        failure_kind="startup",
        failure_code="STARTING",
        failure_message="backend readiness has not succeeded yet",
        failure_status_code=None,
        credential_type=None,
        consecutive_failures=0,
        bypass_grace=False,
    )


def write_backend_readiness_state(path: Path, state: BackendReadinessState) -> None:
    write_atomic_json(path, asdict(state))


def read_backend_readiness_state(path: Path) -> BackendReadinessState | None:
    try:
        payload = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return _backend_readiness_state_from_payload(payload)


def readiness_verdict(
    settings: ReadinessSettings | None = None,
    *,
    now: datetime | None = None,
) -> ReadinessVerdict:
    readiness_settings = settings if settings is not None else default_settings().readiness
    if not readiness_settings.enabled:
        return ReadinessVerdict(ready=True, reason=None, state=None)
    state = read_backend_readiness_state(readiness_settings.state_file)
    if state is None:
        return ReadinessVerdict(ready=False, reason="backend readiness state is missing", state=None)
    unavailable_verdict = _unavailable_state_verdict(state, readiness_settings, now)
    if unavailable_verdict is not None:
        return unavailable_verdict
    return ReadinessVerdict(ready=True, reason=None, state=_state_with_effective_ready(state, readiness_settings))


def _unavailable_state_verdict(
    state: BackendReadinessState,
    settings: ReadinessSettings,
    now: datetime | None,
) -> ReadinessVerdict | None:
    checked_at = _parse_datetime(state.checked_at)
    if checked_at is None:
        return ReadinessVerdict(ready=False, reason="backend readiness state is invalid", state=state)
    current_time = now if now is not None else datetime.now(UTC)
    current_time = current_time.astimezone(UTC)
    reason = _readiness_unavailable_reason(state, settings, checked_at, current_time)
    return None if reason is None else ReadinessVerdict(ready=False, reason=reason, state=state)


def _readiness_unavailable_reason(
    state: BackendReadinessState,
    settings: ReadinessSettings,
    checked_at: datetime,
    current_time: datetime,
) -> str | None:
    if checked_at > current_time + READINESS_CLOCK_ANOMALY_TOLERANCE:
        return "backend readiness state has a clock anomaly"
    age_seconds = int((current_time - checked_at).total_seconds())
    if age_seconds > settings.state_stale_after_seconds:
        return "backend readiness state is stale"
    if state.bypass_grace:
        return "backend readiness has a terminal auth failure"
    if state.last_success_at is None:
        return "backend readiness has not succeeded yet"
    if state.consecutive_failures >= settings.failure_threshold:
        return "backend readiness failure threshold reached"
    return None


def _state_with_effective_ready(
    state: BackendReadinessState,
    settings: ReadinessSettings,
) -> BackendReadinessState:
    if state.consecutive_failures == 0:
        return state
    return replace(state, ready=state.consecutive_failures <= settings.ready_after_failure_count)


def _backend_readiness_state_from_payload(payload: dict[str, object]) -> BackendReadinessState | None:
    ready = payload.get("ready")
    checked_at = payload.get("checked_at")
    last_success_at = payload.get("last_success_at")
    consecutive_failures = payload.get("consecutive_failures")
    if not isinstance(ready, bool) and ready is not None:
        return None
    if not isinstance(checked_at, str) and checked_at is not None:
        return None
    if not isinstance(last_success_at, str) and last_success_at is not None:
        return None
    if not isinstance(consecutive_failures, int):
        return None
    return BackendReadinessState(
        ready=ready,
        checked_at=checked_at,
        last_success_at=last_success_at,
        failure_kind=_optional_str(payload.get("failure_kind")),
        failure_code=_optional_str(payload.get("failure_code")),
        failure_message=_optional_str(payload.get("failure_message")),
        failure_status_code=_optional_int(payload.get("failure_status_code")),
        credential_type=_optional_str(payload.get("credential_type")),
        consecutive_failures=consecutive_failures,
        bypass_grace=_optional_bool(payload.get("bypass_grace")),
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _optional_bool(value: object) -> bool:
    return value if isinstance(value, bool) else False
