"""Catalog-authoritative improvement relationships for Audit workflows."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from enji_guard_cli.audit.ports import (
    AuditAutofixDefinition,
    AuditAutofixJob,
    AuditAutofixUpdate,
    AuditCatalogAutofix,
    AuditCatalogResult,
)
from enji_guard_cli.audit.scheduling import (
    DEFAULT_WORKDAYS,
    ScheduleTimeSelection,
    select_preserved_auto_time,
    validate_cadence,
    validate_time_update,
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
class AutofixWriteResult:
    definition: AuditAutofixDefinition
    status: str
    job: AuditAutofixJob | None


def definitions(catalog: AuditCatalogResult) -> tuple[AuditAutofixDefinition, ...]:
    published_audits = {
        action.action_key for action in catalog.actions if action.category == "audit" and action.status == PUBLISHED
    }
    result = [_definition(item, published_audits) for item in catalog.autofixes if _is_visible(item)]
    return tuple(
        sorted(
            result, key=lambda item: (item.sort_order is None, item.sort_order or 0, item.action_key, item.variant_key)
        )
    )


def select(
    selectors: Sequence[str], available: tuple[AuditAutofixDefinition, ...]
) -> tuple[AuditAutofixDefinition, ...]:
    if not selectors:
        raise ValueError("pass one or more AUTOFIXES or --all")
    wanted = [item.selector for item in available] if tuple(selectors) == ("__all__",) else list(selectors)
    by_selector = {item.selector: item for item in available}
    selected: list[AuditAutofixDefinition] = []
    for selector in wanted:
        match = _selected_definition(by_selector, selector)
        if match not in selected:
            selected.append(match)
    return tuple(selected)


def _selected_definition(by_selector: dict[str, AuditAutofixDefinition], selector: str) -> AuditAutofixDefinition:
    match = by_selector.get(selector)
    if match is None:
        raise ValueError(f"unknown autofix selector: {selector}")
    if not match.supported:
        raise ValueError(f"autofix selector is unsupported until a relationship is defined: {selector}")
    return match


def desired_job(
    existing: AuditAutofixJob | None,
    definition: AuditAutofixDefinition,
    update: AuditAutofixUpdate,
) -> AuditAutofixJob | None:
    validate_autofix_update(update)
    if existing is None and update.enabled is not True:
        return None
    timezone = update.timezone or (existing.timezone if existing else None)
    if timezone is None:
        raise ValueError("pass --timezone when enabling an absent autofix")
    timing = _selected_timing(existing, update)
    return AuditAutofixJob(
        action_key=definition.action_key,
        variant_key=definition.variant_key,
        kind=existing.kind if existing else definition.kind,
        enabled=update.enabled if update.enabled is not None else (existing.enabled if existing else True),
        auto_fix=update.auto_fix if update.auto_fix is not None else (existing.auto_fix if existing else True),
        autofix_variant_key=(existing.autofix_variant_key if existing else None) or definition.variant_key,
        frequency=update.frequency or (existing.frequency if existing else None) or "workdays",
        days_of_week=update.days_of_week if update.days_of_week is not None else (_days(existing) or DEFAULT_WORKDAYS),
        schedule_time=timing.schedule_time,
        schedule_time_source=timing.schedule_time_source,
        timezone=timezone,
        pentest_mode=(existing.pentest_mode if existing else None) or "off",
        extensions=existing.extensions if existing else (),
    )


def set_one(
    definition: AuditAutofixDefinition,
    existing: AuditAutofixJob | None,
    update: AuditAutofixUpdate,
    write: Callable[[str, AuditAutofixJob], AuditAutofixJob],
) -> AutofixWriteResult:
    if not definition.supported:
        raise ValueError(f"autofix selector is unsupported until a relationship is defined: {definition.selector}")
    validate_autofix_update(update)
    if existing is None and update.enabled is False:
        return AutofixWriteResult(definition, "unchanged", None)
    desired = desired_job(existing, definition, update)
    if desired is None:
        return AutofixWriteResult(definition, "unchanged", None)
    if existing is not None and _effective(existing) == _effective(desired):
        return AutofixWriteResult(definition, "unchanged", existing)
    return AutofixWriteResult(definition, "changed", write(definition.kind or definition.selector, desired))


def _is_visible(item: AuditCatalogAutofix) -> bool:
    return item.status == PUBLISHED and item.action_key not in SEPARATE_ACTIONS


def _definition(item: AuditCatalogAutofix, published_audits: set[str]) -> AuditAutofixDefinition:
    source, kind = RELATIONSHIPS.get(item.action_key, (None, None))
    return AuditAutofixDefinition(
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


def _effective(job: AuditAutofixJob) -> tuple[object, ...]:
    # Enji reads improvement jobs back with the short kind (for example,
    # ``dependency-update``), while the catalog uses the namespaced action key
    # (``improvement.dependency-update``).  The endpoint and variant already
    # identify the job, so that wire-format alias is not part of its effective
    # configuration.
    return (
        job.enabled,
        job.auto_fix,
        job.autofix_variant_key,
        job.variant_key,
        job.frequency,
        job.days_of_week,
        job.schedule_time,
        job.schedule_time_source,
        job.timezone,
        job.pentest_mode,
    )


def _days(job: AuditAutofixJob | None) -> tuple[str, ...] | None:
    return job.days_of_week if job and job.days_of_week else None


def _source(job: AuditAutofixJob | None) -> str | None:
    value = job.schedule_time_source if job else None
    return value if value in {"auto", "user"} else None


def validate_autofix_update(update: AuditAutofixUpdate) -> None:
    if _is_empty_update(update):
        raise ValueError("pass --enabled, --auto-fix, --frequency, --days, --time, or --timezone")
    validate_cadence(update.frequency, subject="autofix")
    validate_week_days(update.days_of_week, subject="autofix")
    validate_time_update(update.schedule_time)


def _is_empty_update(update: AuditAutofixUpdate) -> bool:
    return all(
        value is None
        for value in (
            update.enabled,
            update.auto_fix,
            update.frequency,
            update.days_of_week,
            update.schedule_time,
            update.timezone,
        )
    )


def _selected_timing(existing: AuditAutofixJob | None, update: AuditAutofixUpdate) -> ScheduleTimeSelection:
    return select_preserved_auto_time(
        update.schedule_time,
        existing_time=existing.schedule_time if existing else None,
        existing_source=_source(existing),
        auto_default_time="09:00",
    )
