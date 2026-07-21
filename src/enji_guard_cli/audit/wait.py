"""Non-barrier polling for audit completion."""

from collections.abc import Callable
from dataclasses import dataclass

from enji_guard_cli.audit.ports import AuditStatus, AuditWaitOptions, AuditWaitResult


@dataclass(frozen=True, slots=True)
class AuditWaitDependencies:
    read_status: Callable[[str], AuditStatus]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]


def wait_for_completion(
    repo_id: str,
    *,
    options: AuditWaitOptions,
    heartbeat: Callable[[AuditWaitResult], None] | None = None,
    dependencies: AuditWaitDependencies,
) -> AuditWaitResult:
    validate_wait_options(options)
    started_at = dependencies.monotonic()
    deadline = started_at + options.timeout_seconds
    next_heartbeat_at = started_at
    while True:
        status = dependencies.read_status(repo_id)
        now = dependencies.monotonic()
        timed_out = now >= deadline
        result = wait_result(repo_id, status, started_at, timed_out=timed_out, now=now)
        if result.complete or result.reason in {"failed", "missing"} or timed_out:
            return result
        if heartbeat is not None and now >= next_heartbeat_at:
            heartbeat(result)
            next_heartbeat_at += options.heartbeat_seconds
        dependencies.sleep(max(0.0, min(options.poll_seconds, deadline - now)))


def validate_wait_options(options: AuditWaitOptions) -> None:
    if options.poll_seconds <= 0:
        raise ValueError("poll interval must be positive")
    if options.timeout_seconds < 0:
        raise ValueError("timeout must not be negative")
    if options.heartbeat_seconds <= 0:
        raise ValueError("heartbeat interval must be positive")


def wait_result(
    repo_id: str,
    status: AuditStatus,
    started_at: float,
    *,
    timed_out: bool,
    now: float,
) -> AuditWaitResult:
    if status.failed:
        reason = "failed"
    elif timed_out:
        reason = "timeout"
    elif status.active:
        reason = "waiting"
    elif status.missing:
        reason = "missing"
    elif status.complete:
        reason = "complete"
    else:
        reason = "stale"
    return AuditWaitResult(
        repo_id=repo_id,
        status=status,
        complete=status.complete and not timed_out,
        timed_out=timed_out,
        reason=reason,
        elapsed_seconds=round(now - started_at),
    )
