import pytest

from enji_guard_cli.audit.improvement_jobs import (
    definitions,
    desired_job,
    select,
    set_one,
    validate_improvement_job_update,
)
from enji_guard_cli.audit.ports import (
    AuditCatalogAction,
    AuditCatalogResult,
    CatalogImprovement,
    ImprovementDefinition,
    ImprovementJob,
    ImprovementJobUpdate,
)


def _definition(*, supported: bool = True) -> ImprovementDefinition:
    return ImprovementDefinition(
        "improvement.vuln-fix", "default", "Vuln fix", None, "audit.security", "vuln-fix", supported
    )


def _catalog() -> AuditCatalogResult:
    return AuditCatalogResult(
        actions=(AuditCatalogAction("audit.security", "Security", "audit", "published", "vulns", "security"),),
        improvements=(
            CatalogImprovement("improvement.vuln-fix", "default", "Vuln fix", status="published"),
            CatalogImprovement("improvement.pentest", "default", "Pentest", status="published"),
            CatalogImprovement("improvement.vuln-fix", "hidden", "Hidden", status="draft"),
        ),
    )


def test_definitions_exclude_separate_and_unpublished_catalog_entries() -> None:
    result = definitions(_catalog())
    assert [item.variant_key for item in result] == ["default"]
    assert result[0].supported is True


def test_select_supports_all_and_deduplicates() -> None:
    available = (_definition(),)
    assert select(["__all__"], available) == available
    assert select(["vuln-fix", "vuln-fix"], available) == available


@pytest.mark.parametrize(
    ("selectors", "message"),
    [(["missing"], "unknown improvement selector"), (["vuln-fix"], "unsupported")],
)
def test_select_rejects_invalid_or_unsupported(selectors: list[str], message: str) -> None:
    available = (_definition(supported=selectors == ["missing"]),)
    with pytest.raises(ValueError, match=message):
        select(selectors, available)


def test_desired_job_requires_timezone_for_new_enable_and_preserves_defaults() -> None:
    with pytest.raises(ValueError, match="timezone"):
        desired_job(
            None,
            _definition(),
            ImprovementJobUpdate(True),
        )
    result = desired_job(None, _definition(), ImprovementJobUpdate(True, timezone="UTC"))
    assert result is not None
    assert result.enabled is True
    assert result.days_of_week == ("mon", "tue", "wed", "thu", "fri")


def test_set_one_is_idempotent_and_skips_new_disable() -> None:
    definition = _definition()
    assert (
        set_one(definition, None, ImprovementJobUpdate(False), lambda *_: pytest.fail("must not write")).status
        == "unchanged"
    )
    existing = desired_job(None, definition, ImprovementJobUpdate(True, timezone="UTC"))
    assert existing is not None
    assert existing is not None
    result = set_one(definition, existing, ImprovementJobUpdate(True), lambda *_: pytest.fail("must not write"))
    assert result.status == "unchanged"


def test_set_one_treats_enji_short_action_key_as_same_job() -> None:
    existing = ImprovementJob(
        action_key="vuln-fix",
        variant_key="default",
        kind="vuln-fix",
        enabled=True,
        automatic_execution=True,
        provider_variant_key="default",
        frequency="workdays",
        days_of_week=("mon", "tue", "wed", "thu", "fri"),
        schedule_time="09:00",
        schedule_time_source="auto",
        timezone="UTC",
        pentest_mode="off",
    )

    result = set_one(
        _definition(),
        existing,
        ImprovementJobUpdate(True, "workdays", "UTC"),
        lambda *_: pytest.fail("wire action-key alias must not trigger a write"),
    )

    assert result.status == "unchanged"


def test_existing_improvement_job_accepts_schedule_only_update() -> None:
    existing = ImprovementJob(
        action_key="improvement.vuln-fix",
        variant_key="default",
        kind="vuln-fix",
        enabled=True,
        automatic_execution=True,
        provider_variant_key="default",
        frequency="weekly",
        days_of_week=("mon",),
        schedule_time="09:00",
        schedule_time_source="auto",
        timezone="UTC",
        pentest_mode="off",
    )

    desired = desired_job(
        existing,
        _definition(),
        ImprovementJobUpdate(
            enabled=None,
            automatic_execution=False,
            frequency="workdays",
            days_of_week=("tue", "thu"),
            schedule_time="10:30",
        ),
    )

    assert desired is not None
    assert desired.enabled is True
    assert desired.automatic_execution is False
    assert desired.frequency == "workdays"
    assert desired.days_of_week == ("tue", "thu")
    assert desired.schedule_time == "10:30"
    assert desired.schedule_time_source == "user"
    assert desired.timezone == "UTC"


def test_improvement_job_auto_time_restores_auto_source_without_moving_existing_clock() -> None:
    existing = ImprovementJob(
        action_key="improvement.vuln-fix",
        variant_key="default",
        kind="vuln-fix",
        enabled=True,
        automatic_execution=True,
        provider_variant_key="default",
        frequency="weekly",
        days_of_week=("mon",),
        schedule_time="11:15",
        schedule_time_source="user",
        timezone="UTC",
        pentest_mode="off",
    )

    desired = desired_job(existing, _definition(), ImprovementJobUpdate(enabled=None, schedule_time="auto"))

    assert desired is not None
    assert desired.schedule_time == "11:15"
    assert desired.schedule_time_source == "auto"


@pytest.mark.parametrize(
    ("update", "message"),
    [
        (ImprovementJobUpdate(enabled=None), "pass --enabled"),
        (ImprovementJobUpdate(enabled=None, frequency="never"), "unknown improvement job frequency"),
        (ImprovementJobUpdate(enabled=None, days_of_week=()), "one or more improvement job days"),
        (ImprovementJobUpdate(enabled=None, days_of_week=("noday",)), "unknown improvement job day"),
        (ImprovementJobUpdate(enabled=None, days_of_week=("mon", "mon")), "duplicate improvement job day"),
        (ImprovementJobUpdate(enabled=None, schedule_time="25:00"), "schedule time"),
    ],
)
def test_improvement_job_update_validation_rejects_invalid_values(update: ImprovementJobUpdate, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_improvement_job_update(update)


def test_improvement_job_update_validation_accepts_scheduler_controls() -> None:
    validate_improvement_job_update(ImprovementJobUpdate(enabled=None, automatic_execution=False))
    validate_improvement_job_update(ImprovementJobUpdate(enabled=None, days_of_week=("sat",)))
    validate_improvement_job_update(ImprovementJobUpdate(enabled=None, schedule_time="auto"))
