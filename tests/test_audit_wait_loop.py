"""The wait poll loop is the only code that can busy-spin or never return.

`test_audit_wait.py` only covers the already-complete path.  These tests
drive the loop itself over an injected clock -- no wall-clock sleeping --
so the deadline clamp, the heartbeat cadence and every terminal reason
are asserted on what the loop actually did.
"""

from dataclasses import dataclass, field

import pytest

from enji_guard_cli.audit.ports import (
    AuditFreshness,
    AuditFreshnessState,
    AuditStatus,
    AuditStatusItem,
    AuditTaskLifecycle,
    AuditWaitOptions,
    AuditWaitResult,
)
from enji_guard_cli.audit.wait import AuditWaitDependencies, validate_wait_options, wait_for_completion

REPO = "repo_1"


def status(
    *, lifecycle: AuditTaskLifecycle = "none", can_read: bool = True, state: AuditFreshnessState = "fresh"
) -> AuditStatus:
    """One repository status with a single audit in the requested condition."""
    return AuditStatus(
        REPO,
        "sha",
        (
            AuditStatusItem(
                "audit.security",
                "Security",
                AuditFreshness("sha", "sha", state),
                can_read,
                lifecycle,
                None,
                None,
            ),
        ),
    )


RUNNING = status(lifecycle="running")
DONE = status()
FAILED = status(lifecycle="failed")
MISSING = status(can_read=False)
STALE_BUT_DONE = status(state="stale")


@dataclass
class FakeClock:
    """A monotonic clock that only ever advances because the loop slept."""

    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class ScriptedStatusReader:
    """Serve a scripted status sequence, repeating the last entry forever."""

    script: tuple[AuditStatus, ...]
    repo_ids: list[str] = field(default_factory=list)

    def read(self, repo_id: str) -> AuditStatus:
        self.repo_ids.append(repo_id)
        index = min(len(self.repo_ids) - 1, len(self.script) - 1)
        return self.script[index]


@dataclass
class HeartbeatLog:
    """Record when the loop reported progress, in loop time."""

    clock: FakeClock
    at_seconds: list[float] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def record(self, result: AuditWaitResult) -> None:
        self.at_seconds.append(self.clock.now)
        self.reasons.append(result.reason)


def test_the_loop_polls_until_the_audit_completes() -> None:
    clock = FakeClock()
    reader = ScriptedStatusReader((RUNNING, RUNNING, DONE))

    result = wait_for_completion(
        REPO,
        options=AuditWaitOptions(5, 100, 60),
        dependencies=AuditWaitDependencies(reader.read, clock.monotonic, clock.sleep),
    )

    assert reader.repo_ids == [REPO, REPO, REPO]
    assert clock.sleeps == [5, 5]
    assert (result.complete, result.reason, result.timed_out) == (True, "complete", False)
    assert result.elapsed_seconds == 10


def test_the_loop_never_sleeps_past_the_deadline() -> None:
    """The last sleep is clamped to what is left, so the timeout is exact."""
    clock = FakeClock()
    reader = ScriptedStatusReader((RUNNING,))

    result = wait_for_completion(
        REPO,
        options=AuditWaitOptions(7, 20, 60),
        dependencies=AuditWaitDependencies(reader.read, clock.monotonic, clock.sleep),
    )

    assert clock.sleeps == [7, 7, 6]
    assert clock.now == 20
    assert (result.complete, result.timed_out, result.reason) == (False, True, "timeout")
    assert result.elapsed_seconds == 20


def test_a_zero_timeout_returns_without_sleeping_at_all() -> None:
    clock = FakeClock()
    reader = ScriptedStatusReader((RUNNING,))

    result = wait_for_completion(
        REPO,
        options=AuditWaitOptions(5, 0, 60),
        dependencies=AuditWaitDependencies(reader.read, clock.monotonic, clock.sleep),
    )

    assert clock.sleeps == []
    assert reader.repo_ids == [REPO]
    assert (result.timed_out, result.reason) == (True, "timeout")


def test_the_heartbeat_keeps_its_own_cadence_instead_of_drifting_with_the_poll() -> None:
    """Heartbeats are scheduled forward from the previous due time.

    Re-basing the next heartbeat on the current poll instant instead would
    stretch the interval by up to one poll every single beat.
    """
    clock = FakeClock()
    reader = ScriptedStatusReader((RUNNING,))
    log = HeartbeatLog(clock)

    wait_for_completion(
        REPO,
        options=AuditWaitOptions(3, 45, 10),
        heartbeat=log.record,
        dependencies=AuditWaitDependencies(reader.read, clock.monotonic, clock.sleep),
    )

    assert log.at_seconds == [0, 12, 21, 30, 42]
    assert set(log.reasons) == {"waiting"}


def test_no_heartbeat_is_reported_for_the_terminal_result() -> None:
    """The completion is the caller's return value, not a progress report."""
    clock = FakeClock()
    reader = ScriptedStatusReader((DONE,))
    log = HeartbeatLog(clock)

    wait_for_completion(
        REPO,
        options=AuditWaitOptions(3, 45, 10),
        heartbeat=log.record,
        dependencies=AuditWaitDependencies(reader.read, clock.monotonic, clock.sleep),
    )

    assert log.at_seconds == []


@pytest.mark.parametrize(
    ("scripted", "reason"),
    [(FAILED, "failed"), (MISSING, "missing"), (DONE, "complete")],
)
def test_a_terminal_status_stops_the_loop_on_its_first_poll(scripted: AuditStatus, reason: str) -> None:
    clock = FakeClock()
    reader = ScriptedStatusReader((scripted,))

    result = wait_for_completion(
        REPO,
        options=AuditWaitOptions(5, 100, 60),
        dependencies=AuditWaitDependencies(reader.read, clock.monotonic, clock.sleep),
    )

    assert reader.repo_ids == [REPO]
    assert clock.sleeps == []
    assert result.reason == reason
    assert result.complete is (reason == "complete")


def test_a_finished_audit_against_an_older_commit_still_counts_as_complete() -> None:
    """`wait` waits for runs to finish, not for the report to match HEAD."""
    clock = FakeClock()
    reader = ScriptedStatusReader((STALE_BUT_DONE,))

    result = wait_for_completion(
        REPO,
        options=AuditWaitOptions(5, 100, 60),
        dependencies=AuditWaitDependencies(reader.read, clock.monotonic, clock.sleep),
    )

    assert (result.complete, result.reason) == (True, "complete")
    assert result.status.stale == ("audit.security",)


def test_a_failure_that_appears_mid_wait_ends_the_loop() -> None:
    clock = FakeClock()
    reader = ScriptedStatusReader((RUNNING, FAILED))

    result = wait_for_completion(
        REPO,
        options=AuditWaitOptions(5, 100, 60),
        dependencies=AuditWaitDependencies(reader.read, clock.monotonic, clock.sleep),
    )

    assert clock.sleeps == [5]
    assert (result.reason, result.complete, result.timed_out) == ("failed", False, False)


def test_a_completion_that_lands_exactly_on_the_deadline_is_reported_as_a_timeout() -> None:
    """`complete` must stay false once the deadline passed, or `wait` lies."""
    clock = FakeClock()
    reader = ScriptedStatusReader((RUNNING, DONE))

    result = wait_for_completion(
        REPO,
        options=AuditWaitOptions(10, 10, 60),
        dependencies=AuditWaitDependencies(reader.read, clock.monotonic, clock.sleep),
    )

    assert (result.complete, result.timed_out, result.reason) == (False, True, "timeout")


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (AuditWaitOptions(0, 100, 60), "poll interval must be positive"),
        (AuditWaitOptions(-1, 100, 60), "poll interval must be positive"),
        (AuditWaitOptions(5, -1, 60), "timeout must not be negative"),
        (AuditWaitOptions(5, 100, 0), "heartbeat interval must be positive"),
        (AuditWaitOptions(5, 100, -30), "heartbeat interval must be positive"),
    ],
)
def test_unusable_wait_options_are_refused(options: AuditWaitOptions, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_wait_options(options)


@pytest.mark.parametrize(
    "options",
    [AuditWaitOptions(0, 100, 60), AuditWaitOptions(5, -1, 60), AuditWaitOptions(5, 100, 0)],
)
def test_the_loop_validates_before_it_reads_any_status(options: AuditWaitOptions) -> None:
    """A zero poll interval would busy-spin against the backend."""
    clock = FakeClock()
    reader = ScriptedStatusReader((RUNNING,))

    with pytest.raises(ValueError):
        wait_for_completion(
            REPO,
            options=options,
            dependencies=AuditWaitDependencies(reader.read, clock.monotonic, clock.sleep),
        )

    assert reader.repo_ids == []
