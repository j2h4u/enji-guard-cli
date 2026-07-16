import pytest

from enji_guard_cli.audit.ports import AuditSchedule, AuditScheduleUpdate
from enji_guard_cli.audit.schedules import (
    audit_auto_run_key,
    plan_schedule_update,
    validate_schedule_time,
    validate_schedule_update,
)


def test_schedule_preserves_user_time_and_auto_time_resets_it() -> None:
    existing = AuditSchedule("audit.security", True, "workdays", None, 1, "08:30", "user", "UTC")
    preserved = plan_schedule_update(existing, "audit.security", AuditScheduleUpdate(enabled=True))
    automatic = plan_schedule_update(
        existing, "audit.security", AuditScheduleUpdate(enabled=True, schedule_time="auto")
    )
    assert preserved is not None and preserved.schedule_time == "08:30"
    assert automatic is not None and automatic.schedule_time == "00:00"


def test_schedule_requires_exact_audit_action_key() -> None:
    with pytest.raises(ValueError):
        audit_auto_run_key("security")
    assert validate_schedule_time("9:05") == "09:05"


@pytest.mark.parametrize(
    ("update", "message"),
    [
        (AuditScheduleUpdate(), "pass --enabled"),
        (AuditScheduleUpdate(window_days=("mon",)), "pass --frequency"),
        (AuditScheduleUpdate(cadence="daily", window_days=("noday",)), "unknown window"),
        (AuditScheduleUpdate(cadence="daily", window_days=("mon", "mon")), "duplicate"),
        (AuditScheduleUpdate(cadence="never"), "unknown schedule frequency"),
        (AuditScheduleUpdate(schedule_time="25:00"), "schedule time"),
    ],
)
def test_schedule_update_validation_rejects_invalid_values(update: AuditScheduleUpdate, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_schedule_update(update)


def test_schedule_update_validation_accepts_timezone_and_auto_time() -> None:
    validate_schedule_update(AuditScheduleUpdate(timezone="Asia/Almaty"))
    validate_schedule_update(AuditScheduleUpdate(schedule_time="auto"))
