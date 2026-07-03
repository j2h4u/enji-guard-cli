from datetime import UTC, datetime, timedelta
from pathlib import Path

from enji_guard_cli.readiness import (
    INITIAL_BACKEND_READINESS_STATE,
    BackendReadinessProbe,
    backend_readiness_state_after_probe,
    read_backend_readiness_state,
    readiness_verdict,
    write_backend_readiness_state,
)
from enji_guard_cli.settings import ReadinessSettings


def test_backend_readiness_state_tracks_success(tmp_path: Path) -> None:
    checked_at = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    state = backend_readiness_state_after_probe(
        INITIAL_BACKEND_READINESS_STATE,
        BackendReadinessProbe(ready=True, credential_type="cookie", elapsed_ms=12),
        checked_at=checked_at,
    )

    assert state.ready is True
    assert state.checked_at == "2026-07-03T12:00:00+00:00"
    assert state.last_success_at == "2026-07-03T12:00:00+00:00"
    assert state.credential_type == "cookie"
    assert state.consecutive_failures == 0

    state_file = tmp_path / "state" / "readiness.json"
    write_backend_readiness_state(state_file, state)

    assert read_backend_readiness_state(state_file) == state


def test_readiness_verdict_tolerates_failures_below_threshold(tmp_path: Path) -> None:
    settings = readiness_settings(tmp_path)
    now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    first_failure = backend_readiness_state_after_probe(
        INITIAL_BACKEND_READINESS_STATE,
        BackendReadinessProbe(
            ready=False,
            failure_kind="auth",
            failure_code="AUTH_INVALID",
            failure_message="stored credential is not authenticated",
            failure_status_code=401,
            credential_type="cookie",
        ),
        checked_at=now,
    )
    write_backend_readiness_state(settings.state_file, first_failure)

    verdict = readiness_verdict(settings, now=now)

    assert verdict.ready is True
    assert verdict.reason is None
    assert verdict.state is not None
    assert verdict.state.ready is True


def test_readiness_verdict_fails_at_threshold(tmp_path: Path) -> None:
    settings = readiness_settings(tmp_path)
    now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    state = INITIAL_BACKEND_READINESS_STATE
    for offset in range(settings.failure_threshold):
        state = backend_readiness_state_after_probe(
            state,
            BackendReadinessProbe(
                ready=False,
                failure_kind="auth",
                failure_code="AUTH_INVALID",
                failure_message="stored credential is not authenticated",
                failure_status_code=401,
                credential_type="cookie",
            ),
            checked_at=now + timedelta(seconds=offset),
        )
    write_backend_readiness_state(settings.state_file, state)

    verdict = readiness_verdict(settings, now=now + timedelta(seconds=settings.failure_threshold))

    assert verdict.ready is False
    assert verdict.reason == "backend readiness failure threshold reached"


def test_readiness_verdict_fails_when_state_is_stale(tmp_path: Path) -> None:
    settings = readiness_settings(tmp_path)
    checked_at = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    state = backend_readiness_state_after_probe(
        INITIAL_BACKEND_READINESS_STATE,
        BackendReadinessProbe(ready=True, credential_type="cookie"),
        checked_at=checked_at,
    )
    write_backend_readiness_state(settings.state_file, state)

    verdict = readiness_verdict(settings, now=checked_at + timedelta(seconds=61))

    assert verdict.ready is False
    assert verdict.reason == "backend readiness state is stale"


def readiness_settings(tmp_path: Path) -> ReadinessSettings:
    return ReadinessSettings(
        enabled=True,
        state_file=tmp_path / "readiness.json",
        heartbeat_interval_seconds=30,
        heartbeat_timeout_seconds=2.0,
        failure_threshold=3,
        state_stale_after_seconds=60,
    )
