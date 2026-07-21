"""Pre-start audit snapshot preservation warning and summary."""

from dataclasses import dataclass

from enji_guard_cli.audit.ports import AuditStatus

SNAPSHOT_VISIBILITY_RISK_CODE = "SNAPSHOT_VISIBILITY_RISK"
SNAPSHOT_VISIBILITY_RISK_MESSAGE = "starting audits can temporarily hide older artifacts"


@dataclass(frozen=True, slots=True)
class AuditPreflight:
    warning_code: str
    warning_message: str
    readable: tuple[str, ...]
    active: tuple[str, ...]
    queued: tuple[str, ...]
    running: tuple[str, ...]
    stale: tuple[str, ...]
    missing: tuple[str, ...]
    current_head_sha: str | None

    @property
    def ready(self) -> tuple[str, ...]:
        return self.readable


def build_preflight(status: AuditStatus) -> AuditPreflight:
    queued = tuple(item.audit_key for item in status.items if item.task_lifecycle == "queued")
    running = tuple(item.audit_key for item in status.items if item.task_lifecycle == "running")
    return AuditPreflight(
        warning_code=SNAPSHOT_VISIBILITY_RISK_CODE,
        warning_message=SNAPSHOT_VISIBILITY_RISK_MESSAGE,
        readable=status.readable,
        active=status.active,
        queued=queued,
        running=running,
        stale=status.stale,
        missing=status.missing,
        current_head_sha=status.current_head_sha,
    )


def snapshots_to_preserve(status: AuditStatus) -> tuple[str, ...]:
    """Return every currently readable artifact before starting new work."""

    return status.readable
