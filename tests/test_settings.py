from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from enji_guard_cli.settings import (
    AuditWaitSettings,
    AutoRefreshSettings,
    FanoutSettings,
    LogLevelName,
    ReadinessSettings,
    RepoSettings,
    RepositorySortName,
    TelemetrySettings,
    TransportPoolSettings,
    TransportRetrySettings,
    TransportSettings,
    default_settings,
)


def test_default_settings_are_valid() -> None:
    settings = default_settings()

    assert settings.transport.timeout_seconds > 0
    assert settings.telemetry.level_name == "INFO"
    assert settings.fanout.max_concurrency > 0


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: AutoRefreshSettings(
                enabled=True,
                lead_seconds=300,
                fallback_seconds=0,
            ),
            "auto_refresh.fallback_seconds",
        ),
        (
            lambda: TransportSettings(
                timeout_seconds=0,
                retry=TransportRetrySettings(
                    total=3,
                    backoff_factor=0.5,
                    max_delay_seconds=30,
                    jitter_seconds=0.5,
                    retryable_status_codes=(429,),
                    respect_retry_after_header=True,
                ),
            ),
            "transport.timeout_seconds",
        ),
        (
            lambda: TransportRetrySettings(
                total=3,
                backoff_factor=0.5,
                max_delay_seconds=30,
                jitter_seconds=0.5,
                retryable_status_codes=(99,),
                respect_retry_after_header=True,
            ),
            "retryable_status_codes",
        ),
        (
            lambda: TransportPoolSettings(max_connections=2, max_keepalive_connections=3),
            "max_keepalive_connections",
        ),
        (
            lambda: TelemetrySettings(
                level_name=cast(LogLevelName, "TRACE"),
                log_format="json",
                log_file=Path("telemetry.jsonl"),
                max_bytes=10_000,
                backup_count=1,
            ),
            "telemetry.level_name",
        ),
        (
            lambda: ReadinessSettings(
                enabled=True,
                state_file=Path("readiness.json"),
                heartbeat_interval_seconds=300,
                heartbeat_timeout_seconds=5,
                failure_threshold=0,
                state_stale_after_seconds=900,
            ),
            "readiness.failure_threshold",
        ),
        (
            lambda: AuditWaitSettings(
                poll_seconds=30,
                timeout_seconds=10,
                timeout_text="10s",
                heartbeat_seconds=120,
            ),
            "audit_wait.timeout_seconds",
        ),
        (lambda: RepoSettings(default_sort=cast(RepositorySortName, "random")), "repo.default_sort"),
        (lambda: FanoutSettings(max_concurrency=0), "fanout.max_concurrency"),
    ],
)
def test_settings_validation_rejects_invalid_runtime_values(factory: Callable[[], object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()
