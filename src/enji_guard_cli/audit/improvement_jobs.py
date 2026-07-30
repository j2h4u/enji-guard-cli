"""Catalog-authoritative improvement-job relationships for Audit workflows."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from enji_guard_cli.audit.ports import (
    AuditCatalogResult,
    CatalogImprovement,
    ImprovementDefinition,
    ImprovementJob,
    ImprovementJobUpdate,
)
from enji_guard_cli.audit.scheduling import (
    DEFAULT_WORKDAYS,
    ScheduleTimeSelection,
    select_preserved_auto_time,
    validate_cadence,
    validate_time_update,
    validate_timezone,
    validate_week_days,
)

PUBLISHED = "published"
RELATIONSHIPS = {
    "improvement.vuln-fix": ("audit.security", "vuln-fix"),
    "improvement.test-writing": ("audit.tests", "test-writing"),
    "improvement.dependency-update": ("audit.dependency-hygiene", "dependency-update"),
}
SEPARATE_ACTIONS = frozenset({"audit.pentest", "improvement.pentest"})


@dataclass(frozen=True, slots=True)
class ImprovementJobWriteResult:
    definition: ImprovementDefinition
    status: str
    job: ImprovementJob | None


def definitions(catalog: AuditCatalogResult) -> tuple[ImprovementDefinition, ...]:
    published_audits = {
        action.action_key for action in catalog.actions if action.category == "audit" and action.status == PUBLISHED
    }
    result = [_definition(item, published_audits) for item in catalog.improvements if _is_visible(item)]
    return tuple(
        sorted(
            result, key=lambda item: (item.sort_order is None, item.sort_order or 0, item.action_key, item.variant_key)
        )
    )


def select(selectors: Sequence[str], available: tuple[ImprovementDefinition, ...]) -> tuple[ImprovementDefinition, ...]:
    if not selectors:
        raise ValueError("pass one or more IMPROVEMENTS or --all")
    wanted = [item.selector for item in available] if tuple(selectors) == ("__all__",) else list(selectors)
    by_selector = {item.selector: item for item in available}
    selected: list[ImprovementDefinition] = []
    for selector in wanted:
        match = _selected_definition(by_selector, selector)
        if match not in selected:
            selected.append(match)
    return tuple(selected)


def _selected_definition(by_selector: dict[str, ImprovementDefinition], selector: str) -> ImprovementDefinition:
    match = by_selector.get(selector)
    if match is None:
        raise ValueError(f"unknown improvement selector: {selector}")
    if not match.supported:
        raise ValueError(f"improvement selector is unsupported until a relationship is defined: {selector}")
    return match


def desired_job(
    existing: ImprovementJob | None,
    definition: ImprovementDefinition,
    update: ImprovementJobUpdate,
) -> ImprovementJob | None:
    validate_improvement_job_update(update)
    if existing is None and update.enabled is not True:
        return None
    timezone = update.timezone or (existing.timezone if existing else None)
    if timezone is None:
        raise ValueError("pass --timezone when enabling an absent improvement job")
    timing = _selected_timing(existing, update)
    return ImprovementJob(
        action_key=definition.action_key,
        variant_key=definition.variant_key,
        kind=existing.kind if existing else definition.kind,
        enabled=update.enabled if update.enabled is not None else (existing.enabled if existing else True),
        automatic_execution=(
            update.automatic_execution
            if update.automatic_execution is not None
            else (existing.automatic_execution if existing else True)
        ),
        provider_variant_key=(existing.provider_variant_key if existing else None) or definition.variant_key,
        frequency=update.frequency or (existing.frequency if existing else None) or "workdays",
        days_of_week=update.days_of_week if update.days_of_week is not None else (_days(existing) or DEFAULT_WORKDAYS),
        schedule_time=timing.schedule_time,
        schedule_time_source=timing.schedule_time_source,
        timezone=timezone,
        pentest_mode=(existing.pentest_mode if existing else None) or "off",
    )


def set_one(
    definition: ImprovementDefinition,
    existing: ImprovementJob | None,
    update: ImprovementJobUpdate,
    write: Callable[[str, ImprovementJob], ImprovementJob],
) -> ImprovementJobWriteResult:
    if not definition.supported:
        raise ValueError(f"improvement selector is unsupported until a relationship is defined: {definition.selector}")
    validate_improvement_job_update(update)
    if existing is None and update.enabled is False:
        return ImprovementJobWriteResult(definition, "unchanged", None)
    desired = desired_job(existing, definition, update)
    if desired is None:
        return ImprovementJobWriteResult(definition, "unchanged", None)
    if existing is not None and _effective(existing) == _effective(desired):
        return ImprovementJobWriteResult(definition, "unchanged", existing)
    return ImprovementJobWriteResult(definition, "changed", write(definition.kind or definition.selector, desired))


def _is_visible(item: CatalogImprovement) -> bool:
    return item.status == PUBLISHED and item.action_key not in SEPARATE_ACTIONS


def _definition(item: CatalogImprovement, published_audits: set[str]) -> ImprovementDefinition:
    source, kind = RELATIONSHIPS.get(item.action_key, (None, None))
    return ImprovementDefinition(
        action_key=item.action_key,
        variant_key=item.variant_key,
        title=item.title,
        description=item.description,
        source_audit=source,
        kind=kind,
        supported=source in published_audits if source else False,
        runbook_id=item.runbook_id,
        sort_order=item.sort_order,
    )


def _effective(job: ImprovementJob) -> tuple[object, ...]:
    # Enji reads improvement jobs back with the short kind (for example,
    # ``dependency-update``), while the catalog uses the namespaced action key
    # (``improvement.dependency-update``).  The endpoint and variant already
    # identify the job, so that wire-format alias is not part of its effective
    # configuration.
    return (
        job.enabled,
        job.automatic_execution,
        job.provider_variant_key,
        job.variant_key,
        job.frequency,
        job.days_of_week,
        job.schedule_time,
        job.schedule_time_source,
        job.timezone,
        job.pentest_mode,
    )


def _days(job: ImprovementJob | None) -> tuple[str, ...] | None:
    return job.days_of_week if job and job.days_of_week else None


def _source(job: ImprovementJob | None) -> str | None:
    value = job.schedule_time_source if job else None
    return value if value in {"auto", "user"} else None


def validate_improvement_job_update(update: ImprovementJobUpdate) -> None:
    if _is_empty_update(update):
        raise ValueError("pass --enabled, --automatic-execution, --frequency, --days, --time, or --timezone")
    validate_cadence(update.frequency, subject="improvement job")
    validate_week_days(update.days_of_week, subject="improvement job")
    validate_time_update(update.schedule_time)
    validate_timezone(update.timezone)


def _is_empty_update(update: ImprovementJobUpdate) -> bool:
    return all(
        value is None
        for value in (
            update.enabled,
            update.automatic_execution,
            update.frequency,
            update.days_of_week,
            update.schedule_time,
            update.timezone,
        )
    )


def _selected_timing(existing: ImprovementJob | None, update: ImprovementJobUpdate) -> ScheduleTimeSelection:
    return select_preserved_auto_time(
        update.schedule_time,
        existing_time=existing.schedule_time if existing else None,
        existing_source=_source(existing),
        auto_default_time="09:00",
    )
