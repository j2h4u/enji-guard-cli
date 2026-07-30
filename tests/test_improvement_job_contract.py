"""Stable application contract for improvement jobs."""

from dataclasses import asdict

from enji_guard_cli.application.subscription_views import ImprovementJobView, improvement_job_view
from enji_guard_cli.audit.ports import ImprovementJob


def test_improvement_job_view_exposes_product_fields_not_provider_extensions() -> None:
    view = improvement_job_view(
        ImprovementJob(
            action_key="improvement.vuln-fix",
            variant_key="default",
            kind="vuln-fix",
            enabled=True,
            automatic_execution=True,
            provider_variant_key="default",
            frequency="weekly",
            days_of_week=("sat",),
            schedule_time="09:00",
            schedule_time_source="auto",
            timezone="UTC",
            pentest_mode="off",
        )
    )

    assert isinstance(view, ImprovementJobView)
    assert asdict(view) == {
        "action_key": "improvement.vuln-fix",
        "variant_key": "default",
        "kind": "vuln-fix",
        "enabled": True,
        "automatic_execution": True,
        "provider_variant_key": "default",
        "frequency": "weekly",
        "days_of_week": ("sat",),
        "schedule_time": "09:00",
        "schedule_time_source": "auto",
        "timezone": "UTC",
        "pentest_mode": "off",
    }
