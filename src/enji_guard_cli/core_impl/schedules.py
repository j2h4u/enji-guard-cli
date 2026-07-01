from typing import cast

from enji_guard_cli.audits import AuditDefinition
from enji_guard_cli.core_impl.email_preferences import repo_count
from enji_guard_cli.core_impl.models import (
    ALL_SCHEDULE_DAYS,
    DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY,
    MAX_SCHEDULE_HOUR,
    MAX_SCHEDULE_MINUTE,
    SCHEDULE_TIME_PARTS,
    RepoTargetPayload,
    ScheduleFrequency,
    ScheduleSettingsUpdate,
    ScheduleUpdate,
)
from enji_guard_cli.core_impl.payloads import (
    json_bool,
    json_list_of_str,
    json_object_list,
    json_str,
    json_str_values,
)
from enji_guard_cli.enji_api import JsonObjectPayload, JsonValue


def validate_schedule_settings_update(update: ScheduleSettingsUpdate) -> None:
    if (
        update.enabled is None
        and update.frequency is None
        and update.days_of_week is None
        and update.schedule_time is None
        and update.timezone is None
    ):
        raise ValueError("pass --enabled, --frequency, or --timezone")
    if update.days_of_week is not None and update.frequency is None:
        raise ValueError("pass --frequency when overriding days")


def schedule_settings_payload_for_job(
    existing: JsonObjectPayload | None,
    update: ScheduleSettingsUpdate,
) -> JsonObjectPayload | None:
    if existing is None and update.enabled is not True:
        return None
    existing_frequency = json_str(existing.get("frequency")) if existing is not None else None
    frequency = schedule_frequency(update.frequency or existing_frequency)
    if frequency is None:
        frequency = "weekly"
    schedule_time = selected_schedule_time(existing, update)
    existing_timezone = json_str(existing.get("timezone")) if existing is not None else None
    timezone = update.timezone or existing_timezone
    base = dict(existing) if existing is not None else {}
    desired = {
        **base,
        **schedule_payload(
            ScheduleUpdate(
                enabled=schedule_enabled(existing, update),
                auto_fix=schedule_auto_fix(existing),
                frequency=frequency,
                days_of_week=selected_schedule_days(existing, update, frequency),
                schedule_time=schedule_time,
                timezone=timezone or "UTC",
            )
        ),
        "autofixVariantKey": json_str(base.get("autofixVariantKey")) or "default",
    }
    if update.schedule_time == "auto":
        desired.pop("scheduleTime", None)
    return desired


def schedule_enabled(existing: JsonObjectPayload | None, update: ScheduleSettingsUpdate) -> bool:
    if update.enabled is not None:
        return update.enabled
    if existing is None:
        return False
    return json_bool(existing.get("enabled")) is True


def schedule_auto_fix(existing: JsonObjectPayload | None) -> bool:
    if existing is None:
        return False
    return json_bool(existing.get("autoFix")) is True


def selected_schedule_days(
    existing: JsonObjectPayload | None,
    update: ScheduleSettingsUpdate,
    frequency: ScheduleFrequency,
) -> list[str]:
    if update.days_of_week is not None:
        return update.days_of_week
    if update.frequency is not None or existing is None:
        return list(DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY[frequency])
    return json_list_of_str(existing.get("daysOfWeek")) or list(DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY[frequency])


def selected_schedule_time(existing: JsonObjectPayload | None, update: ScheduleSettingsUpdate) -> str:
    if update.schedule_time is not None:
        return update.schedule_time
    if existing is not None and json_str(existing.get("scheduleTimeSource")) == "user":
        return json_str(existing.get("scheduleTime")) or "auto"
    return "auto"


def schedule_setting_row(
    target: RepoTargetPayload,
    audit: AuditDefinition,
    job: JsonObjectPayload | None,
    *,
    changed: bool | None = None,
    status: str | None = None,
) -> dict[str, JsonValue]:
    row: dict[str, JsonValue] = {
        "project_id": target["project_id"],
        "project_name": target["project_name"],
        "repo_id": target["repo_id"],
        "github_repo": target["github_repo"],
        "audit": audit.alias.value,
        "job_kind": audit.job_kind,
        "configured": job is not None,
        "enabled": False,
        "frequency": None,
        "days_of_week": [],
        "schedule_time": None,
        "schedule_time_source": None,
        "timezone": None,
        "auto_fix": False,
    }
    if job is not None:
        row.update(schedule_setting_fields(job))
    if changed is not None:
        row["changed"] = changed
    if status is not None:
        row["status"] = status
    return row


def schedule_setting_fields(job: JsonObjectPayload) -> dict[str, JsonValue]:
    schedule_time_source = json_str(job.get("scheduleTimeSource"))
    return {
        "enabled": json_bool(job.get("enabled")) is True,
        "frequency": schedule_frequency(json_str(job.get("frequency"))),
        "days_of_week": json_str_values(json_list_of_str(job.get("daysOfWeek"))),
        "schedule_time": json_str(job.get("scheduleTime")),
        "schedule_time_source": schedule_time_source,
        "timezone": json_str(job.get("timezone")),
        "auto_fix": json_bool(job.get("autoFix")) is True,
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


def schedule_effective_state(job: JsonObjectPayload) -> JsonObjectPayload:
    return {
        "enabled": json_bool(job.get("enabled")) is True,
        "autoFix": json_bool(job.get("autoFix")) is True,
        "autofixVariantKey": json_str(job.get("autofixVariantKey")) or "default",
        "frequency": schedule_frequency(json_str(job.get("frequency"))) or "weekly",
        "daysOfWeek": json_str_values(json_list_of_str(job.get("daysOfWeek"))),
        "scheduleTime": json_str(job.get("scheduleTime")),
        "scheduleTimeSource": json_str(job.get("scheduleTimeSource")) or "auto",
        "timezone": json_str(job.get("timezone")) or "UTC",
    }


def schedule_payload(update: ScheduleUpdate) -> JsonObjectPayload:
    validate_days_of_week(update.days_of_week)
    validate_days_for_frequency(update.frequency, update.days_of_week)
    payload: JsonObjectPayload = {
        "enabled": update.enabled,
        "autoFix": update.auto_fix,
        "autofixVariantKey": "default",
        "frequency": update.frequency,
        "daysOfWeek": json_str_values(update.days_of_week),
        "scheduleTimeSource": "auto" if update.schedule_time == "auto" else "user",
        "timezone": update.timezone,
    }
    if update.schedule_time != "auto":
        payload["scheduleTime"] = validated_schedule_time(update.schedule_time)
    return payload


def schedule_job_by_kind(payload: JsonObjectPayload, job_kind: str | None) -> JsonObjectPayload | None:
    if job_kind is None:
        raise ValueError("recon does not have a schedulable improvement job")
    for job in json_object_list(payload.get("jobs")):
        if json_str(job.get("kind")) == job_kind:
            return job
    return None


def validate_days_of_week(days_of_week: list[str]) -> None:
    if not days_of_week:
        raise ValueError("days_of_week must not be empty")
    invalid_days = [day for day in days_of_week if day not in ALL_SCHEDULE_DAYS]
    if invalid_days:
        raise ValueError(f"unknown day(s): {', '.join(invalid_days)}")
    duplicate_days = sorted({day for day in days_of_week if days_of_week.count(day) > 1})
    if duplicate_days:
        raise ValueError(f"duplicate day(s): {', '.join(duplicate_days)}")


def validate_days_for_frequency(frequency: ScheduleFrequency, days_of_week: list[str]) -> None:
    expected_count = len(DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY[frequency])
    if len(days_of_week) != expected_count:
        raise ValueError(f"{frequency} expects {expected_count} day(s)")


def schedule_frequency(value: str | None) -> ScheduleFrequency | None:
    if value in DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY:
        return cast(ScheduleFrequency, value)
    return None


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
