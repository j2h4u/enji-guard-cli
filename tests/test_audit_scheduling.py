import pytest

from enji_guard_cli.audit.scheduling import (
    select_preserved_auto_time,
    select_schedule_time,
    validate_cadence,
    validate_schedule_time,
    validate_week_days,
)


def test_schedule_time_validation_normalizes_hh_mm() -> None:
    assert validate_schedule_time("9:05") == "09:05"


@pytest.mark.parametrize("value", ["25:00", "12:99", "soon"])
def test_schedule_time_validation_rejects_invalid_clock(value: str) -> None:
    with pytest.raises(ValueError, match="schedule time"):
        validate_schedule_time(value)


def test_cadence_and_weekday_validation_use_domain_subject_in_errors() -> None:
    with pytest.raises(ValueError, match="unknown improvement job frequency"):
        validate_cadence("never", subject="improvement job")
    with pytest.raises(ValueError, match="pass one or more window days"):
        validate_week_days((), subject="window")
    with pytest.raises(ValueError, match="duplicate improvement job day"):
        validate_week_days(("mon", "mon"), subject="improvement job")


def test_schedule_time_selection_preserves_user_time_and_restores_auto_default() -> None:
    preserved = select_schedule_time(
        None,
        existing_time="08:30",
        existing_source="user",
        auto_default_time="00:00",
    )
    restored = select_schedule_time(
        "auto",
        existing_time="08:30",
        existing_source="user",
        auto_default_time="00:00",
    )

    assert (preserved.schedule_time, preserved.schedule_time_source) == ("08:30", "user")
    assert (restored.schedule_time, restored.schedule_time_source) == ("00:00", "auto")


def test_preserved_auto_time_keeps_existing_clock_for_improvement_jobs() -> None:
    selected = select_preserved_auto_time(
        "auto",
        existing_time="11:15",
        existing_source="user",
        auto_default_time="09:00",
    )

    assert (selected.schedule_time, selected.schedule_time_source) == ("11:15", "auto")
