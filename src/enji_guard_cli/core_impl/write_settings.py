from collections.abc import Callable
from dataclasses import dataclass

from enji_guard_cli.audits import AuditCatalog, AuditDefinition
from enji_guard_cli.core_impl.email_preferences import (
    email_preference_row,
    email_preferences_patch,
    email_preferences_payload,
)
from enji_guard_cli.core_impl.models import EmailPreferenceUpdate, RepoTargetPayload, ScheduleSettingsUpdate
from enji_guard_cli.core_impl.payloads import json_dict
from enji_guard_cli.core_impl.schedules import (
    schedule_effective_state,
    schedule_setting_row,
    schedule_settings_payload,
    schedule_settings_payload_for_subscription,
    schedule_subscription_by_action_key,
    validate_schedule_settings_update,
)
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

type SelectedRepoTargets = Callable[[str | None, str | None], list[RepoTargetPayload]]
type ValidateWriteScope = Callable[[str | None, str | None], None]
type GetAuditEmailPreferences = Callable[[str, str], JsonObjectPayload]
type PutAuditEmailPreferences = Callable[[str, str, JsonObjectPayload], JsonObjectPayload]
type ListSchedules = Callable[[str], JsonObjectPayload]
type SetSchedule = Callable[[str, AuditDefinition, JsonObjectPayload], JsonObjectPayload]


@dataclass(frozen=True, slots=True)
class EmailReadDependencies:
    selected_repo_targets: SelectedRepoTargets
    get_audit_email_preferences: GetAuditEmailPreferences


@dataclass(frozen=True, slots=True)
class EmailWriteDependencies:
    get_audit_email_preferences: GetAuditEmailPreferences
    put_audit_email_preferences: PutAuditEmailPreferences


@dataclass(frozen=True, slots=True)
class ScheduleReadDependencies:
    selected_repo_targets: SelectedRepoTargets
    list_schedules: ListSchedules


@dataclass(frozen=True, slots=True)
class ScheduleWriteDependencies:
    list_schedules: ListSchedules
    set_schedule: SetSchedule


@dataclass(frozen=True, slots=True)
class WriteScopeDependencies:
    validate_write_scope: ValidateWriteScope
    selected_repo_targets: SelectedRepoTargets


@dataclass(frozen=True, slots=True)
class SetEmailPreferencesContext:
    repo: str | None
    project: str | None
    update: EmailPreferenceUpdate
    catalog: AuditCatalog


@dataclass(frozen=True, slots=True)
class SetScheduleSettingsContext:
    repo: str | None
    project: str | None
    update: ScheduleSettingsUpdate
    catalog: AuditCatalog


def list_email_preferences(
    repo: str | None,
    project: str | None,
    *,
    dependencies: EmailReadDependencies,
    catalog: AuditCatalog,
) -> JsonObjectPayload:
    return email_preferences_payload(
        [
            email_preference_row(
                target,
                audit,
                dependencies.get_audit_email_preferences(target["repo_id"], audit.action_key),
            )
            for target in dependencies.selected_repo_targets(repo, project)
            for audit in catalog.report_audits
        ]
    )


def set_email_preferences(
    context: SetEmailPreferencesContext,
    *,
    selected_write_repo_targets: SelectedRepoTargets,
    dependencies: EmailWriteDependencies,
) -> JsonObjectPayload:
    patch = email_preferences_patch(context.update)
    return email_preferences_payload(
        [
            set_email_preference(target, audit, patch, dependencies=dependencies)
            for target in selected_write_repo_targets(context.repo, context.project)
            for audit in context.catalog.report_audits
        ]
    )


def set_email_preference(
    target: RepoTargetPayload,
    audit: AuditDefinition,
    patch: JsonObjectPayload,
    *,
    dependencies: EmailWriteDependencies,
) -> dict[str, JsonValue]:
    existing = dependencies.get_audit_email_preferences(target["repo_id"], audit.action_key)
    if _email_preference_matches(existing, patch):
        return {
            **email_preference_row(target, audit, existing),
            "changed": False,
            "status": "unchanged",
        }
    response = dependencies.put_audit_email_preferences(target["repo_id"], audit.action_key, patch)
    return {
        **email_preference_row(target, audit, response),
        "changed": True,
        "status": "changed",
    }


def _email_preference_matches(payload: JsonObjectPayload, patch: JsonObjectPayload) -> bool:
    resolved = json_dict(payload.get("resolved"))
    return all(resolved.get(key) == value for key, value in patch.items())


def list_schedule_settings(
    repo: str | None,
    project: str | None,
    *,
    dependencies: ScheduleReadDependencies,
    catalog: AuditCatalog,
) -> JsonObjectPayload:
    rows = [
        schedule_setting_row(target, audit, schedule_subscription_by_action_key(subscriptions, audit.action_key))
        for target in dependencies.selected_repo_targets(repo, project)
        for subscriptions in (dependencies.list_schedules(target["repo_id"]),)
        for audit in catalog.report_audits
    ]
    return schedule_settings_payload(rows)


def set_schedule_settings(
    context: SetScheduleSettingsContext,
    *,
    selected_write_repo_targets: SelectedRepoTargets,
    dependencies: ScheduleWriteDependencies,
) -> JsonObjectPayload:
    validate_schedule_settings_update(context.update)
    rows = [
        set_schedule_setting(target, audit, subscriptions, context.update, set_schedule=dependencies.set_schedule)
        for target in selected_write_repo_targets(context.repo, context.project)
        for subscriptions in (dependencies.list_schedules(target["repo_id"]),)
        for audit in context.catalog.report_audits
    ]
    return schedule_settings_payload(rows)


def selected_write_repo_targets(
    repo: str | None,
    project: str | None,
    *,
    all_repos: bool,
    all_projects: bool,
    dependencies: WriteScopeDependencies,
) -> list[RepoTargetPayload]:
    dependencies.validate_write_scope(repo, project)
    if all_projects:
        return dependencies.selected_repo_targets(None, None)
    if all_repos:
        return dependencies.selected_repo_targets(None, project)
    if repo is None:
        raise AssertionError("write scope validation should require repo when no batch flag is set")
    return dependencies.selected_repo_targets(repo, project)


def set_schedule_setting(
    target: RepoTargetPayload,
    audit: AuditDefinition,
    subscriptions: JsonObjectPayload,
    update: ScheduleSettingsUpdate,
    *,
    set_schedule: SetSchedule,
) -> dict[str, JsonValue]:
    existing = schedule_subscription_by_action_key(subscriptions, audit.action_key)
    desired = schedule_settings_payload_for_subscription(existing, update)
    if desired is None:
        return schedule_setting_row(target, audit, existing, changed=False, status="unchanged")
    if existing is not None and schedule_effective_state(existing) == schedule_effective_state(desired):
        return schedule_setting_row(target, audit, existing, changed=False, status="unchanged")
    response = set_schedule(target["repo_id"], audit, desired)
    resolved = json_dict(response.get("subscription")) or desired
    return schedule_setting_row(target, audit, resolved, changed=True, status="changed")
