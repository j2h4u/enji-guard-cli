from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from enji_guard_cli.audit import published_audit_action_keys, published_autofix_keys
from enji_guard_cli.core_impl.models import AutofixSettingsUpdate, RepoTargetPayload, ScheduleCadence
from enji_guard_cli.core_impl.payloads import json_bool, json_list_of_str, json_object_list, json_str, json_str_values
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

type SelectedRepoTargets = Callable[[str | None, str | None], list[RepoTargetPayload]]
type ListImprovementJobs = Callable[[str], JsonObjectPayload]
type PutImprovementJob = Callable[[str, str, JsonObjectPayload], JsonObjectPayload]


@dataclass(frozen=True, slots=True)
class AutofixRelationship:
    action_key: str
    source_audit: str
    kind: str


AUTOFIX_RELATIONSHIPS = (
    AutofixRelationship("improvement.vuln-fix", "audit.security", "vuln-fix"),
    AutofixRelationship("improvement.test-writing", "audit.tests", "test-writing"),
    AutofixRelationship("improvement.dependency-update", "audit.dependency-hygiene", "dependency-update"),
)
PENTEST_AUTOFIX_ACTIONS = frozenset({"audit.pentest", "improvement.pentest"})
DEFAULT_AUTOFIX_FREQUENCY: ScheduleCadence = "workdays"
DEFAULT_AUTOFIX_DAYS = ["mon", "tue", "wed", "thu", "fri"]


@dataclass(frozen=True, slots=True)
class AutofixDefinition:
    action_key: str
    variant_key: str
    source_audit: str | None
    kind: str | None
    supported: bool

    @property
    def selector(self) -> str:
        return self.kind or self.action_key.removeprefix("improvement.")


@dataclass(frozen=True, slots=True)
class AutofixWriteContext:
    repo: str | None
    project: str | None
    selectors: list[str]
    update: AutofixSettingsUpdate


@dataclass(frozen=True, slots=True)
class AutofixWriteDependencies:
    selected_write_repo_targets: SelectedRepoTargets
    list_improvement_jobs: ListImprovementJobs
    put_improvement_job: PutImprovementJob


def autofix_definitions(catalog: JsonObjectPayload) -> tuple[AutofixDefinition, ...]:
    published_audits = published_audit_action_keys(catalog)
    declared = published_autofix_keys(catalog)
    return tuple(
        _definition(action_key, variant_key, published_audits)
        for action_key, variant_key in declared
        if action_key not in PENTEST_AUTOFIX_ACTIONS
    )


def list_autofixes(
    repo: str | None,
    project: str | None,
    *,
    selected_repo_targets: SelectedRepoTargets,
    list_improvement_jobs: ListImprovementJobs,
    definitions: tuple[AutofixDefinition, ...],
) -> JsonObjectPayload:
    rows = [
        autofix_row(target, definition, _job_by_kind(list_improvement_jobs(target["repo_id"]), definition.kind))
        for target in selected_repo_targets(repo, project)
        for definition in definitions
    ]
    return autofix_payload(rows)


def set_autofixes(
    context: AutofixWriteContext,
    *,
    dependencies: AutofixWriteDependencies,
    definitions: tuple[AutofixDefinition, ...],
) -> JsonObjectPayload:
    if context.update.enabled is None:
        raise ValueError("pass --enabled on or off")
    selected = select_autofixes(context.selectors, definitions)
    return autofix_payload(
        [
            set_autofix(
                target,
                definition,
                _job_by_kind(jobs, definition.kind),
                context.update,
                dependencies.put_improvement_job,
            )
            for target in dependencies.selected_write_repo_targets(context.repo, context.project)
            for jobs in (dependencies.list_improvement_jobs(target["repo_id"]),)
            for definition in selected
        ]
    )


def select_autofixes(selectors: list[str], definitions: tuple[AutofixDefinition, ...]) -> tuple[AutofixDefinition, ...]:
    if not selectors:
        raise ValueError("pass one or more AUTOFIXES or --all")
    if selectors == ["__all__"]:
        selectors = [definition.selector for definition in definitions]
    selected: list[AutofixDefinition] = []
    for selector in selectors:
        matches = [definition for definition in definitions if definition.selector == selector]
        if not matches:
            raise ValueError(f"unknown autofix selector: {selector}")
        definition = matches[0]
        if not definition.supported:
            raise ValueError(f"autofix selector is unsupported until a relationship is defined: {selector}")
        if definition not in selected:
            selected.append(definition)
    return tuple(selected)


def autofix_row(
    target: RepoTargetPayload,
    definition: AutofixDefinition,
    job: JsonObjectPayload | None,
    *,
    changed: bool | None = None,
) -> dict[str, JsonValue]:
    row: dict[str, JsonValue] = {
        "project_name": target["project_name"],
        "github_repo": target["github_repo"],
        "source_audit": definition.source_audit,
        "action_key": definition.action_key,
        "variant_key": definition.variant_key,
        "autofix": definition.selector,
        "supported": definition.supported,
        "enabled": json_bool(job.get("enabled")) is True if job else False,
        "frequency": json_str(job.get("frequency")) if job else None,
        "schedule_time": json_str(job.get("scheduleTime")) if job else None,
        "schedule_time_source": json_str(job.get("scheduleTimeSource")) if job else None,
        "timezone": json_str(job.get("timezone")) if job else None,
    }
    if changed is not None:
        row["status"] = "changed" if changed else "unchanged"
    return row


def set_autofix(
    target: RepoTargetPayload,
    definition: AutofixDefinition,
    existing: JsonObjectPayload | None,
    update: AutofixSettingsUpdate,
    put_improvement_job: PutImprovementJob,
) -> dict[str, JsonValue]:
    if definition.kind is None:
        raise AssertionError("supported autofix must have a job kind")
    if existing is None and update.enabled is False:
        return autofix_row(target, definition, None, changed=False)
    desired = autofix_job_payload(existing, definition, update)
    if existing is not None and _effective_job_state(existing) == _effective_job_state(desired):
        return autofix_row(target, definition, existing, changed=False)
    response = put_improvement_job(target["repo_id"], definition.kind, desired)
    resolved = _job_from_response(response) or desired
    return autofix_row(target, definition, resolved, changed=True)


def autofix_job_payload(
    existing: JsonObjectPayload | None,
    definition: AutofixDefinition,
    update: AutofixSettingsUpdate,
) -> JsonObjectPayload:
    timezone = update.timezone or (json_str(existing.get("timezone")) if existing else None)
    if timezone is None:
        raise ValueError("pass --timezone when enabling an absent autofix")
    frequency = update.frequency or _frequency(existing) or DEFAULT_AUTOFIX_FREQUENCY
    schedule_time = json_str(existing.get("scheduleTime")) if existing else None
    schedule_time_source = json_str(existing.get("scheduleTimeSource")) if existing else None
    return {
        **(existing or {}),
        "enabled": update.enabled,
        "autoFix": True,
        "autofixVariantKey": json_str(existing.get("autofixVariantKey")) if existing else definition.variant_key,
        "frequency": frequency,
        "daysOfWeek": json_str_values(json_list_of_str(existing.get("daysOfWeek")))
        if existing
        else json_str_values(DEFAULT_AUTOFIX_DAYS),
        "scheduleTime": schedule_time or "09:00",
        "scheduleTimeSource": schedule_time_source or "auto",
        "timezone": timezone,
        "pentestMode": json_str(existing.get("pentestMode")) if existing else "off",
    }


def autofix_payload(rows: list[dict[str, JsonValue]]) -> JsonObjectPayload:
    return {
        "autofixes": [cast(JsonValue, row) for row in rows],
        "summary": {
            "repo_count": len({json_str(row.get("github_repo")) for row in rows}),
            "autofix_count": len(rows),
            "enabled_count": sum(row.get("enabled") is True for row in rows),
            "changed_count": sum(row.get("status") == "changed" for row in rows),
            "unchanged_count": sum(row.get("status") == "unchanged" for row in rows),
        },
    }


def _definition(action_key: str, variant_key: str, published: set[str]) -> AutofixDefinition:
    relationship = next(
        (candidate for candidate in AUTOFIX_RELATIONSHIPS if candidate.action_key == action_key),
        None,
    )
    if relationship is None:
        return AutofixDefinition(action_key, variant_key, None, None, False)
    return AutofixDefinition(
        action_key,
        variant_key,
        relationship.source_audit,
        relationship.kind,
        relationship.source_audit in published,
    )


def _job_by_kind(payload: JsonObjectPayload, kind: str | None) -> JsonObjectPayload | None:
    if kind is None:
        return None
    return next((job for job in json_object_list(payload.get("jobs")) if json_str(job.get("kind")) == kind), None)


def _job_from_response(response: JsonObjectPayload) -> JsonObjectPayload | None:
    job = response.get("job")
    return job if isinstance(job, dict) else None


def _frequency(job: JsonObjectPayload | None) -> ScheduleCadence | None:
    value = json_str(job.get("frequency")) if job else None
    valid = {"daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"}
    return cast(ScheduleCadence, value) if value in valid else None


def _effective_job_state(job: JsonObjectPayload) -> JsonObjectPayload:
    fields = (
        "enabled",
        "autoFix",
        "autofixVariantKey",
        "frequency",
        "daysOfWeek",
        "scheduleTime",
        "scheduleTimeSource",
        "timezone",
        "pentestMode",
    )
    return {field: job.get(field) for field in fields}
