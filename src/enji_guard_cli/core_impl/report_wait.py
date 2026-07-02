from collections.abc import Callable
from dataclasses import dataclass

from enji_guard_cli.core_impl.models import (
    ReportStatusPayload,
    ReportWaitOptions,
    ReportWaitPayload,
)
from enji_guard_cli.core_impl.repo_status import (
    report_wait_payload,
    validate_report_wait_options,
)


@dataclass(frozen=True, slots=True)
class ReportWaitDependencies:
    read_status: Callable[[str], ReportStatusPayload]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]


def wait_for_report_completion(
    repo_id: str,
    *,
    options: ReportWaitOptions,
    heartbeat: Callable[[ReportWaitPayload], None] | None,
    dependencies: ReportWaitDependencies,
) -> ReportWaitPayload:
    validate_report_wait_options(options)
    started_at = dependencies.monotonic()
    deadline = started_at + options.timeout_seconds
    next_heartbeat_at = started_at
    while True:
        status = dependencies.read_status(repo_id)
        payload = report_wait_payload(repo_id, status, started_at, timed_out=False)
        if payload["complete"] or payload["reason"] == "failed":
            return payload
        now = dependencies.monotonic()
        if now >= deadline:
            return report_wait_payload(repo_id, status, started_at, timed_out=True)
        if heartbeat is not None and now >= next_heartbeat_at:
            heartbeat(payload)
            next_heartbeat_at += options.heartbeat_seconds
        dependencies.sleep(_next_poll_sleep(deadline, options.poll_seconds, now))


def _next_poll_sleep(deadline: float, poll_seconds: int, now: float) -> float:
    return max(0.0, min(float(poll_seconds), deadline - now))
