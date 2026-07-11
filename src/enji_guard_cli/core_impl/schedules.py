from typing import cast

from enji_guard_cli.audits import AuditDefinition
from enji_guard_cli.core_impl.email_preferences import repo_count
from enji_guard_cli.core_impl.models import (
    ALL_SCHEDULE_DAYS,
    DEFAULT_SCHEDULE_DAYS_BY_CADENCE,
    MAX_SCHEDULE_HOUR,
    MAX_SCHEDULE_MINUTE,
    SCHEDULE_TIME_PARTS,
    RepoTargetPayload,
    ScheduleCadence,
    ScheduleSettingsUpdate,
)
from enji_guard_cli.core_impl.payloads import (
    json_bool,
    json_int,
    json_list_of_str,
    json_object_list,
    json_str,
    json_str_values,
)
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

AUDIT_AUTO_RUN_FIELDS = (
    "cadence",
    "enabled",
    "scheduleDay",
    "scheduleDayOfMonth",
    "scheduleTime",
    "scheduleTimeSource",
    "timezone",
    "windowDays",
    "windowEndTime",
    "windowMode",
    "windowStartTime",
)


def validate_schedule_settings_update(update: ScheduleSettingsUpdate) -> None:
    if (
        update.enabled is None
        and update.cadence is None
        and update.window_days is None
        and update.schedule_time is None
        and update.timezone is None
    ):
        raise ValueError("pass --enabled, --frequency, or --timezone")
    if update.window_days is not None and update.cadence is None:
        raise ValueError("pass --frequency when overriding window days")


def schedule_settings_payload_for_subscription(
    existing: JsonObjectPayload | None,
    update: ScheduleSettingsUpdate,
) -> JsonObjectPayload | None:
    if existing is None and update.enabled is not True:
        return None

    existing_cadence = json_str(existing.get("cadence")) if existing else None
    cadence = schedule_cadence(update.cadence or existing_cadence)
    if cadence is None:
        cadence = "workdays"
    existing_enabled = json_bool(existing.get("enabled")) is True if existing else False
    enabled = update.enabled if update.enabled is not None else existing_enabled
    existing_timezone = json_str(existing.get("timezone")) if existing else None
    timezone = update.timezone or existing_timezone
    schedule_time, schedule_time_source = selected_schedule_time(existing, update)
    return {
        "cadence": cadence,
        "enabled": enabled,
        "scheduleDay": json_str(existing.get("scheduleDay")) if existing else None,
        "scheduleDayOfMonth": json_int(existing.get("scheduleDayOfMonth")) if existing else 1,
        "scheduleTime": schedule_time,
        "scheduleTimeSource": schedule_time_source,
        "timezone": timezone or "UTC",
        "windowDays": selected_window_days(existing, update),
        "windowEndTime": json_str(existing.get("windowEndTime")) if existing else None,
        "windowMode": json_str(existing.get("windowMode")) if existing else "anytime",
        "windowStartTime": json_str(existing.get("windowStartTime")) if existing else None,
    }


def selected_schedule_time(
    existing: JsonObjectPayload | None,
    update: ScheduleSettingsUpdate,
) -> tuple[str, str]:
    if update.schedule_time == "auto":
        return "00:00", "auto"
    if update.schedule_time is not None:
        return validated_schedule_time(update.schedule_time), "user"
    if existing is not None and json_str(existing.get("scheduleTimeSource")) == "user":
        return json_str(existing.get("scheduleTime")) or "00:00", "user"
    return "00:00", "auto"


def selected_window_days(existing: JsonObjectPayload | None, update: ScheduleSettingsUpdate) -> list[JsonValue]:
    if update.window_days is not None:
        validate_window_days(update.window_days)
        return json_str_values(update.window_days)
    if update.cadence is not None or existing is None:
        return []
    return json_str_values(json_list_of_str(existing.get("windowDays")))


def schedule_setting_row(
    target: RepoTargetPayload,
    audit: AuditDefinition,
    subscription: JsonObjectPayload | None,
    *,
    changed: bool | None = None,
    status: str | None = None,
) -> dict[str, JsonValue]:
    row: dict[str, JsonValue] = {
        "project_id": target["project_id"],
        "project_name": target["project_name"],
        "repo_id": target["repo_id"],
        "github_repo": target["github_repo"],
        "audit": audit.action_key,
        "configured": subscription is not None,
        "enabled": False,
        "cadence": None,
        "schedule_day": None,
        "schedule_day_of_month": None,
        "schedule_time": None,
        "schedule_time_source": None,
        "timezone": None,
        "window_days": [],
        "window_end_time": None,
        "window_mode": None,
        "window_start_time": None,
    }
    if subscription is not None:
        row.update(schedule_setting_fields(subscription))
    if changed is not None:
        row["changed"] = changed
    if status is not None:
        row["status"] = status
    return row


def schedule_setting_fields(subscription: JsonObjectPayload) -> dict[str, JsonValue]:
    return {
        "enabled": json_bool(subscription.get("enabled")) is True,
        "cadence": schedule_cadence(json_str(subscription.get("cadence"))),
        "schedule_day": json_str(subscription.get("scheduleDay")),
        "schedule_day_of_month": json_int(subscription.get("scheduleDayOfMonth")),
        "schedule_time": json_str(subscription.get("scheduleTime")),
        "schedule_time_source": json_str(subscription.get("scheduleTimeSource")),
        "timezone": json_str(subscription.get("timezone")),
        "window_days": json_str_values(json_list_of_str(subscription.get("windowDays"))),
        "window_end_time": json_str(subscription.get("windowEndTime")),
        "window_mode": json_str(subscription.get("windowMode")),
        "window_start_time": json_str(subscription.get("windowStartTime")),
    }


def schedule_settings_payload(rows: list[dict[str, JsonValue]]) -> JsonObjectPayload:
    schedules = [cast(JsonValue, row) for row in rows]
    return {
        "schedules": schedules,
        "summary": {
            "repo_count": repo_count(rows),
            "audit_count": len(rows),
            "enabled_count": sum(1 for row in rows if row.get("enabled") is True),
            "changed_count": sum(1 for row in rows if row.get("changed") is True),
            "unchanged_count": sum(1 for row in rows if row.get("changed") is False),
        },
    }


def schedule_effective_state(subscription: JsonObjectPayload) -> JsonObjectPayload:
    return {field: subscription.get(field) for field in AUDIT_AUTO_RUN_FIELDS}


def schedule_subscription_by_action_key(
    payload: JsonObjectPayload,
    action_key: str,
) -> JsonObjectPayload | None:
    for subscription in json_object_list(payload.get("subscriptions")):
        if json_str(subscription.get("actionKey")) == action_key:
            return subscription
    return None


def schedule_cadence(value: str | None) -> ScheduleCadence | None:
    if value in DEFAULT_SCHEDULE_DAYS_BY_CADENCE:
        return cast(ScheduleCadence, value)
    return None


def validate_window_days(window_days: list[str]) -> None:
    invalid_days = [day for day in window_days if day not in ALL_SCHEDULE_DAYS]
    if invalid_days:
        raise ValueError(f"unknown window day(s): {', '.join(invalid_days)}")
    duplicate_days = sorted({day for day in window_days if window_days.count(day) > 1})
    if duplicate_days:
        raise ValueError(f"duplicate window day(s): {', '.join(duplicate_days)}")


def validated_schedule_time(value: str) -> str:
    parts = value.split(":", 1)
    if len(parts) != SCHEDULE_TIME_PARTS:
        raise ValueError("schedule time must be auto or HH:MM")
    hour, minute = parts
    if not hour.isdigit() or not minute.isdigit():
        raise ValueError("schedule time must be auto or HH:MM")
    hour_int = int(hour)
    minute_int = int(minute)
    if hour_int > MAX_SCHEDULE_HOUR or minute_int > MAX_SCHEDULE_MINUTE:
        raise ValueError("schedule time must be auto or HH:MM")
    return f"{hour_int:02d}:{minute_int:02d}"
