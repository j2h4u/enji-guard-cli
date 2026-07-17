import pytest

from enji_guard_cli.audit.ports import AuditSchedule, AuditScheduleUpdate
from enji_guard_cli.audit.schedules import (
    audit_auto_run_key,
    list_for_targets,
    plan_schedule_update,
    validate_schedule_time,
    validate_schedule_update,
)
from enji_guard_cli.portfolio.models import RepositoryRef


class _ScheduleGateway:
    def __init__(self, schedules: tuple[AuditSchedule, ...]) -> None:
        self.schedules = schedules
        self.list_calls: list[str] = []

    def list_schedules(self, repo_id: str) -> tuple[AuditSchedule, ...]:
        self.list_calls.append(repo_id)
        return self.schedules

    def set_schedule(self, repo_id: str, audit_key: str, schedule: AuditSchedule) -> AuditSchedule:
        raise AssertionError((repo_id, audit_key, schedule))


def test_schedule_listing_fetches_each_repository_once() -> None:
    current = AuditSchedule("audit.security", True, "daily", None, 1, "08:00", "user", "UTC")
    gateway = _ScheduleGateway((current,))
    target = RepositoryRef("repo-1", "project-1", "Pets", "acme/cat")

    result = list_for_targets((target,), ("audit.security", "audit.tests"), gateway)

    assert gateway.list_calls == ["repo-1"]
    assert result[0].schedules[0] == current
    assert result[0].schedules[1].audit_key == "audit.tests"


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
