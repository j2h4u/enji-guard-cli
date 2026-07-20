from __future__ import annotations

import pytest
from scripts import release_smoke, release_smoke_soak


def test_soak_records_bounded_probe_failures() -> None:
    calls = 0

    def probe(_settings: release_smoke.DockerSmokeSettings) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ()
        raise release_smoke.SmokeFailure("transient")

    settings = release_smoke_soak.SoakSettings(
        smoke=release_smoke.DockerSmokeSettings(repo="owner/repo"),
        duration_seconds=0,
        interval_seconds=0.001,
        max_failures=1,
    )
    code, metrics = release_smoke_soak.run_soak(settings, probe=probe)

    assert code == 0
    assert metrics.iterations == 1
    assert metrics.failures == 0


def test_soak_stops_after_failure_budget() -> None:
    def probe(_settings: release_smoke.DockerSmokeSettings) -> tuple[str, ...]:
        raise release_smoke.SmokeFailure("broken")

    settings = release_smoke_soak.SoakSettings(
        smoke=release_smoke.DockerSmokeSettings(repo="owner/repo"),
        duration_seconds=10,
        interval_seconds=0.001,
        max_failures=0,
    )
    code, metrics = release_smoke_soak.run_soak(settings, probe=probe)

    assert code == 1
    assert metrics.iterations == 1
    assert metrics.failures == 1


def test_soak_rejects_non_positive_interval() -> None:
    settings = release_smoke_soak.SoakSettings(
        smoke=release_smoke.DockerSmokeSettings(repo="owner/repo"),
        duration_seconds=0,
        interval_seconds=0,
    )
    with pytest.raises(ValueError):
        release_smoke_soak.run_soak(settings, probe=lambda _settings: ())
