"""Audit scheduling rules and idempotent update planning."""

from dataclasses import replace
from typing import Literal, cast

from enji_guard_cli.audit.ports import AuditSchedule, AuditScheduleUpdate

CADENCES = frozenset({"daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"})
WEEK_DAYS = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})
TIME_PARTS = 2
MAX_HOUR = 23
MAX_MINUTE = 59


def audit_auto_run_key(action_key: str) -> str:
    if not action_key.startswith("audit.") or len(action_key) == len("audit."):
        raise ValueError(f"schedule action key must be an exact audit action key: {action_key}")
    return action_key


def validate_schedule_update(update: AuditScheduleUpdate) -> None:
    if _is_empty_update(update):
        raise ValueError("pass --enabled, --frequency, or --timezone")
    _validate_window(update)
    _validate_cadence(update.cadence)
    _validate_time(update.schedule_time)


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
    invalid = [day for day in update.window_days if day not in WEEK_DAYS]
    if invalid:
        raise ValueError(f"unknown window day(s): {', '.join(invalid)}")
    duplicate = sorted({day for day in update.window_days if update.window_days.count(day) > 1})
    if duplicate:
        raise ValueError(f"duplicate window day(s): {', '.join(duplicate)}")


def _validate_cadence(cadence: str | None) -> None:
    if cadence is not None and cadence not in CADENCES:
        raise ValueError(f"unknown schedule frequency: {cadence}")


def _validate_time(schedule_time: str | None) -> None:
    if schedule_time is not None and schedule_time != "auto":
        validate_schedule_time(schedule_time)


def validate_schedule_time(value: str) -> str:
    parts = value.split(":", 1)
    if len(parts) != TIME_PARTS or not all(part.isdigit() for part in parts):
        raise ValueError("schedule time must be auto or HH:MM")
    hour, minute = (int(part) for part in parts)
    if hour > MAX_HOUR or minute > MAX_MINUTE:
        raise ValueError("schedule time must be auto or HH:MM")
    return f"{hour:02d}:{minute:02d}"


def selected_schedule_time(existing: AuditSchedule | None, update: AuditScheduleUpdate) -> tuple[str, str]:
    if update.schedule_time == "auto":
        return "00:00", "auto"
    if update.schedule_time is not None:
        return validate_schedule_time(update.schedule_time), "user"
    if existing is not None and existing.schedule_time_source == "user":
        return existing.schedule_time or "00:00", "user"
    return "00:00", "auto"


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


def auto_time(existing: AuditSchedule, *, timezone: str | None = None) -> AuditSchedule:
    """Restore the service-assigned schedule time without changing cadence."""

    return replace(existing, schedule_time="00:00", schedule_time_source="auto", timezone=timezone or existing.timezone)
