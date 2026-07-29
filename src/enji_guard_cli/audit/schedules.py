"""Audit scheduling rules and idempotent update planning."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast

from enji_guard_cli.audit.ports import AuditSchedule, AuditScheduleUpdate
from enji_guard_cli.audit.scheduling import (
    select_schedule_time,
    validate_cadence,
    validate_time_update,
    validate_week_days,
)
from enji_guard_cli.fanout import BoundedFanout


class AuditScheduleTarget(Protocol):
    @property
    def repo_id(self) -> str: ...


class AuditScheduleGateway(Protocol):
    def list_schedules(self, repo_id: str) -> tuple[AuditSchedule, ...]: ...

    def set_schedule(self, repo_id: str, audit_key: str, schedule: AuditSchedule) -> AuditSchedule: ...


@dataclass(frozen=True, slots=True)
class ScheduleTargetResult:
    repo_id: str
    schedules: tuple[AuditSchedule, ...]


def list_for_targets(
    targets: Sequence[AuditScheduleTarget],
    published_audits: Sequence[str],
    gateway: AuditScheduleGateway,
    fanout: BoundedFanout,
) -> tuple[ScheduleTargetResult, ...]:
    """Project configured and unconfigured rows for every selected target."""

    def read_target(target: AuditScheduleTarget) -> ScheduleTargetResult:
        schedules = gateway.list_schedules(target.repo_id)
        return ScheduleTargetResult(
            target.repo_id,
            tuple(schedule_for_audit(audit_key, schedules) for audit_key in published_audits),
        )

    return fanout.map(targets, read_target)


def set_for_targets(
    targets: Sequence[AuditScheduleTarget],
    published_audits: Sequence[str],
    update: AuditScheduleUpdate,
    gateway: AuditScheduleGateway,
) -> tuple[AuditSchedule, ...]:
    validate_schedule_update(update)
    result: list[AuditSchedule] = []
    for target in targets:
        existing = {item.audit_key: item for item in gateway.list_schedules(target.repo_id)}
        for audit_key in published_audits:
            desired = plan_schedule_update(existing.get(audit_key), audit_key, update)
            if desired is None:
                continue
            current = existing.get(audit_key)
            result.append(desired if current == desired else gateway.set_schedule(target.repo_id, audit_key, desired))
    return tuple(result)


def auto_time_for_targets(
    targets: Sequence[AuditScheduleTarget],
    published_audits: Sequence[str],
    gateway: AuditScheduleGateway,
) -> tuple[AuditSchedule, ...]:
    published = frozenset(published_audits)
    result: list[AuditSchedule] = []
    for target in targets:
        for current in gateway.list_schedules(target.repo_id):
            if current.audit_key not in published:
                continue
            desired = auto_time(current)
            result.append(
                current if desired == current else gateway.set_schedule(target.repo_id, current.audit_key, desired)
            )
    return tuple(result)


def audit_auto_run_key(action_key: str) -> str:
    if not action_key.startswith("audit.") or len(action_key) == len("audit."):
        raise ValueError(f"schedule action key must be an exact audit action key: {action_key}")
    return action_key


def validate_schedule_update(update: AuditScheduleUpdate) -> None:
    if _is_empty_update(update):
        raise ValueError("pass --enabled, --frequency, or --timezone")
    _validate_window(update)
    validate_cadence(update.cadence, subject="schedule")
    validate_time_update(update.schedule_time)


def _is_empty_update(update: AuditScheduleUpdate) -> bool:
    return all(
        value is None
        for value in (update.enabled, update.cadence, update.window_days, update.schedule_time, update.timezone)
    )


def _validate_window(update: AuditScheduleUpdate) -> None:
    if update.window_days is None:
        return
    if update.cadence is None:
        raise ValueError("pass --frequency when overriding window days")
    validate_week_days(update.window_days, subject="window")


def selected_schedule_time(existing: AuditSchedule | None, update: AuditScheduleUpdate) -> tuple[str, str]:
    selected = select_schedule_time(
        update.schedule_time,
        existing_time=existing.schedule_time if existing else None,
        existing_source=existing.schedule_time_source if existing else None,
        auto_default_time="00:00",
    )
    return selected.schedule_time, selected.schedule_time_source


def plan_schedule_update(
    existing: AuditSchedule | None, audit_key: str, update: AuditScheduleUpdate
) -> AuditSchedule | None:
    validate_schedule_update(update)
    audit_auto_run_key(audit_key)
    if existing is None and update.enabled is not True:
        return None
    time, source = selected_schedule_time(existing, update)
    cadence = update.cadence or (existing.cadence if existing else None) or "workdays"
    window_days = update.window_days if update.window_days is not None else (existing.window_days if existing else ())
    return AuditSchedule(
        audit_key=audit_key,
        enabled=update.enabled if update.enabled is not None else (existing.enabled if existing else False),
        cadence=cadence,
        schedule_day=existing.schedule_day if existing else None,
        schedule_day_of_month=existing.schedule_day_of_month if existing else 1,
        schedule_time=time,
        schedule_time_source=cast(Literal["auto", "user"], source),
        timezone=update.timezone or (existing.timezone if existing else None) or "UTC",
        window_days=tuple(window_days),
        window_start_time=existing.window_start_time if existing else None,
        window_end_time=existing.window_end_time if existing else None,
        window_mode=existing.window_mode if existing else "anytime",
    )


def schedule_for_audit(audit_key: str, schedules: tuple[AuditSchedule, ...]) -> AuditSchedule:
    """Project one configured or unconfigured row for a published audit."""

    current = next((item for item in schedules if item.audit_key == audit_key), None)
    return current or AuditSchedule(audit_key, False, None, None, None, None, None, None)


def auto_time(existing: AuditSchedule, *, timezone: str | None = None) -> AuditSchedule:
    """Restore the service-assigned schedule time without changing cadence."""

    return replace(existing, schedule_time="00:00", schedule_time_source="auto", timezone=timezone or existing.timezone)
