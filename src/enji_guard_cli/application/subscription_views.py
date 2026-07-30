"""Application-owned presentation of recurring audit subscriptions.

Like :mod:`enji_guard_cli.application.audit_views`, these are new types mapped
from :mod:`enji_guard_cli.audit` objects -- never aliases or re-exports of
them.  Field names match the domain because they are the operator-visible
``--json`` contract.

A domain type held in a *field* is coupling neither ``tach`` nor a grep over
import statements can see: nothing imports it at the delivery surface, yet
renaming it there breaks the CLI.  Mapping onto these views keeps that from
being true.
"""

from dataclasses import dataclass
from typing import Literal

from enji_guard_cli.audit.ports import AuditSchedule, ImprovementDefinition, ImprovementJob


@dataclass(frozen=True, slots=True)
class AuditScheduleView:
    """When one published audit re-runs on its own."""

    audit_key: str
    enabled: bool
    cadence: str | None
    schedule_day: str | None
    schedule_day_of_month: int | None
    schedule_time: str | None
    schedule_time_source: Literal["auto", "user"] | None
    timezone: str | None
    window_days: tuple[str, ...] = ()
    window_start_time: str | None = None
    window_end_time: str | None = None
    window_mode: str | None = None


@dataclass(frozen=True, slots=True)
class ImprovementDefinitionView:
    """An improvement the catalog offers, whether or not it is configured."""

    action_key: str
    variant_key: str
    title: str | None
    description: str | None
    source_audit: str | None
    kind: str | None
    supported: bool
    runbook_id: str | None = None
    sort_order: int | None = None

    @property
    def selector(self) -> str:
        """The short name operators type for this improvement."""
        return self.kind or self.action_key.removeprefix("improvement.")


@dataclass(frozen=True, slots=True)
class ImprovementJobView:
    """The recurring settings attached to a configured improvement."""

    action_key: str
    variant_key: str
    kind: str | None = None
    enabled: bool | None = None
    automatic_execution: bool | None = None
    provider_variant_key: str | None = None
    frequency: str | None = None
    days_of_week: tuple[str, ...] = ()
    schedule_time: str | None = None
    schedule_time_source: Literal["auto", "user"] | None = None
    timezone: str | None = None
    pentest_mode: str | None = None


def schedule_view(schedule: AuditSchedule) -> AuditScheduleView:
    return AuditScheduleView(
        audit_key=schedule.audit_key,
        enabled=schedule.enabled,
        cadence=schedule.cadence,
        schedule_day=schedule.schedule_day,
        schedule_day_of_month=schedule.schedule_day_of_month,
        schedule_time=schedule.schedule_time,
        schedule_time_source=schedule.schedule_time_source,
        timezone=schedule.timezone,
        window_days=schedule.window_days,
        window_start_time=schedule.window_start_time,
        window_end_time=schedule.window_end_time,
        window_mode=schedule.window_mode,
    )


def improvement_definition_view(definition: ImprovementDefinition) -> ImprovementDefinitionView:
    return ImprovementDefinitionView(
        action_key=definition.action_key,
        variant_key=definition.variant_key,
        title=definition.title,
        description=definition.description,
        source_audit=definition.source_audit,
        kind=definition.kind,
        supported=definition.supported,
        runbook_id=definition.runbook_id,
        sort_order=definition.sort_order,
    )


def improvement_job_view(job: ImprovementJob) -> ImprovementJobView:
    return ImprovementJobView(
        action_key=job.action_key,
        variant_key=job.variant_key,
        kind=job.kind,
        enabled=job.enabled,
        automatic_execution=job.automatic_execution,
        provider_variant_key=job.provider_variant_key,
        frequency=job.frequency,
        days_of_week=job.days_of_week,
        schedule_time=job.schedule_time,
        schedule_time_source=job.schedule_time_source,
        timezone=job.timezone,
        pentest_mode=job.pentest_mode,
    )


__all__ = [
    "AuditScheduleView",
    "ImprovementDefinitionView",
    "ImprovementJobView",
    "improvement_definition_view",
    "improvement_job_view",
    "schedule_view",
]
