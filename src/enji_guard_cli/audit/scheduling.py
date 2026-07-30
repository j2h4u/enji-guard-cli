"""Shared scheduling policy for audit subscriptions and improvement jobs."""

from dataclasses import dataclass
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CADENCES = frozenset({"daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"})
WEEK_DAYS = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})
DEFAULT_WORKDAYS = ("mon", "tue", "wed", "thu", "fri")
AUTO_TIME_SOURCE: Literal["auto"] = "auto"
USER_TIME_SOURCE: Literal["user"] = "user"
TIME_PARTS = 2
MAX_HOUR = 23
MAX_MINUTE = 59


@dataclass(frozen=True, slots=True)
class ScheduleTimeSelection:
    """A normalized clock value plus the source Enji stores next to it."""

    schedule_time: str
    schedule_time_source: Literal["auto", "user"]


def validate_timezone(value: str | None) -> None:
    """Require an explicit timezone to be an installed IANA identifier."""

    if value is None:
        return
    try:
        ZoneInfo(value)
    except ValueError, ZoneInfoNotFoundError:
        raise ValueError(f"unknown IANA timezone: {value}") from None


def validate_cadence(value: str | None, *, subject: str) -> None:
    if value is not None and value not in CADENCES:
        raise ValueError(f"unknown {subject} frequency: {value}")


def validate_week_days(days: tuple[str, ...] | None, *, subject: str) -> None:
    if days is None:
        return
    if not days:
        raise ValueError(f"pass one or more {subject} days")
    invalid = [day for day in days if day not in WEEK_DAYS]
    if invalid:
        raise ValueError(f"unknown {subject} day(s): {', '.join(invalid)}")
    duplicate = sorted({day for day in days if days.count(day) > 1})
    if duplicate:
        raise ValueError(f"duplicate {subject} day(s): {', '.join(duplicate)}")


def validate_schedule_time(value: str) -> str:
    parts = value.split(":", 1)
    if len(parts) != TIME_PARTS or not all(part.isdigit() for part in parts):
        raise ValueError("schedule time must be auto or HH:MM")
    hour, minute = (int(part) for part in parts)
    if hour > MAX_HOUR or minute > MAX_MINUTE:
        raise ValueError("schedule time must be auto or HH:MM")
    return f"{hour:02d}:{minute:02d}"


def validate_time_update(value: str | None) -> None:
    if value is not None and value != AUTO_TIME_SOURCE:
        validate_schedule_time(value)


def select_schedule_time(
    requested: str | None,
    *,
    existing_time: str | None,
    existing_source: str | None,
    auto_default_time: str,
) -> ScheduleTimeSelection:
    if requested == AUTO_TIME_SOURCE:
        return ScheduleTimeSelection(auto_default_time, AUTO_TIME_SOURCE)
    if requested is not None:
        return ScheduleTimeSelection(validate_schedule_time(requested), USER_TIME_SOURCE)
    if existing_source == USER_TIME_SOURCE:
        return ScheduleTimeSelection(existing_time or auto_default_time, USER_TIME_SOURCE)
    return ScheduleTimeSelection(auto_default_time, AUTO_TIME_SOURCE)


def select_preserved_auto_time(
    requested: str | None,
    *,
    existing_time: str | None,
    existing_source: str | None,
    auto_default_time: str,
) -> ScheduleTimeSelection:
    if requested == AUTO_TIME_SOURCE:
        return ScheduleTimeSelection(existing_time or auto_default_time, AUTO_TIME_SOURCE)
    return select_schedule_time(
        requested,
        existing_time=existing_time,
        existing_source=existing_source,
        auto_default_time=auto_default_time,
    )
