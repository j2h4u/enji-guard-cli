from collections.abc import Callable
from dataclasses import dataclass

from enji_guard_cli.audits import REPORT_AUDITS, AuditAlias, ReportAuditDefinition
from enji_guard_cli.core_impl.email_preferences import (
    email_preference_row,
    email_preferences_patch,
    email_preferences_payload,
)
from enji_guard_cli.core_impl.models import EmailPreferenceUpdate, RepoTargetPayload, ScheduleSettingsUpdate
from enji_guard_cli.core_impl.payloads import json_dict
from enji_guard_cli.core_impl.schedules import (
    schedule_effective_state,
    schedule_job_by_kind,
    schedule_setting_row,
    schedule_settings_payload,
    schedule_settings_payload_for_job,
    validate_schedule_settings_update,
)
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

type SelectedRepoTargets = Callable[[str | None, str | None], list[RepoTargetPayload]]
type ValidateWriteScope = Callable[[str | None, str | None], None]
type GetAuditEmailPreferences = Callable[[str, str], JsonObjectPayload]
type PutAuditEmailPreferences = Callable[[str, str, JsonObjectPayload], JsonObjectPayload]
type ListSchedules = Callable[[str], JsonObjectPayload]
type SetSchedule = Callable[[str, AuditAlias, JsonObjectPayload], JsonObjectPayload]


@dataclass(frozen=True, slots=True)
class EmailReadDependencies:
    selected_repo_targets: SelectedRepoTargets
    get_audit_email_preferences: GetAuditEmailPreferences


@dataclass(frozen=True, slots=True)
class EmailWriteDependencies:
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


def list_email_preferences(
    repo: str | None,
    project: str | None,
    *,
    dependencies: EmailReadDependencies,
) -> JsonObjectPayload:
    return email_preferences_payload(
        [
            email_preference_row(
                target,
                audit,
                dependencies.get_audit_email_preferences(target["repo_id"], audit.action_key),
            )
            for target in dependencies.selected_repo_targets(repo, project)
            for audit in REPORT_AUDITS
        ]
    )


def set_email_preferences(
    repo: str | None,
    project: str | None,
    update: EmailPreferenceUpdate,
    *,
    selected_write_repo_targets: SelectedRepoTargets,
    dependencies: EmailWriteDependencies,
) -> JsonObjectPayload:
    patch = email_preferences_patch(update)
    return email_preferences_payload(
        [
            email_preference_row(
                target,
                audit,
                dependencies.put_audit_email_preferences(target["repo_id"], audit.action_key, patch),
            )
            for target in selected_write_repo_targets(repo, project)
            for audit in REPORT_AUDITS
        ]
    )


def list_schedule_settings(
    repo: str | None,
    project: str | None,
    *,
    dependencies: ScheduleReadDependencies,
) -> JsonObjectPayload:
    rows = [
        schedule_setting_row(target, audit, schedule_job_by_kind(jobs, audit.job_kind))
        for target in dependencies.selected_repo_targets(repo, project)
        for jobs in (dependencies.list_schedules(target["repo_id"]),)
        for audit in REPORT_AUDITS
    ]
    return schedule_settings_payload(rows)


def set_schedule_settings(
    repo: str | None,
    project: str | None,
    update: ScheduleSettingsUpdate,
    *,
    selected_write_repo_targets: SelectedRepoTargets,
    dependencies: ScheduleWriteDependencies,
) -> JsonObjectPayload:
    validate_schedule_settings_update(update)
    rows = [
        set_schedule_setting(target, audit, jobs, update, set_schedule=dependencies.set_schedule)
        for target in selected_write_repo_targets(repo, project)
        for jobs in (dependencies.list_schedules(target["repo_id"]),)
        for audit in REPORT_AUDITS
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
    audit: ReportAuditDefinition,
    jobs: JsonObjectPayload,
    update: ScheduleSettingsUpdate,
    *,
    set_schedule: SetSchedule,
) -> dict[str, JsonValue]:
    existing = schedule_job_by_kind(jobs, audit.job_kind)
    desired = schedule_settings_payload_for_job(existing, update)
    if desired is None:
        return schedule_setting_row(target, audit, existing, changed=False, status="unchanged")
    if existing is not None and schedule_effective_state(existing) == schedule_effective_state(desired):
        return schedule_setting_row(target, audit, existing, changed=False, status="unchanged")
    response = set_schedule(target["repo_id"], audit.alias, desired)
    resolved = json_dict(response.get("job")) or desired
    return schedule_setting_row(target, audit, resolved, changed=True, status="changed")
