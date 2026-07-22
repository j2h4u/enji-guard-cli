"""Semantic human presenters for CLI DTOs."""

from __future__ import annotations

import json
from collections.abc import Mapping

from enji_guard_cli.application import (
    AuditRead,
    AuditSummary,
    AutofixListing,
    AutofixListingItem,
    ProjectSettings,
    RepositoryRef,
    RepositoryStatus,
    ScheduleListing,
)
from enji_guard_cli.audit.ports import AuditWaitResult
from enji_guard_cli.delivery.cli.audit_presenter import render_audit_read
from enji_guard_cli.delivery.cli.presentation import CliPresentation, json_projection
from enji_guard_cli.gitlab.models import GitLabCredentialsResult, GitLabProjectsResult
from enji_guard_cli.portfolio.models import ProjectRef
from enji_guard_cli.portfolio.status import PortfolioOverview


def repository_label(repository: RepositoryRef) -> str:
    return f"{repository.identity.provider.value}@{repository.identity.host}:{repository.identity.locator}"


def state_label(value: bool | None) -> str:
    return "ready" if value is True else "pending" if value is False else "unknown"


def portfolio_text(payload: PortfolioOverview) -> str:
    lines = [f"observed_at: {payload.observed_at}"]
    if not payload.projects:
        return "\n".join([*lines, "No projects found."])
    for project in payload.projects:
        lines.append(f"\n{project.project.name or project.project.project_id}")
        for item in project.repositories:
            repository = item.repository
            scores = [float(score) for score in repository.scores.values() if score is not None]
            weakest = f"{min(scores):g}" if scores else "-"
            overall = f"{sum(scores) / len(scores):.1f}" if scores else "-"
            active = sum(run.completed_at is None for run in item.active_runs)
            lines.append(
                f"  {repository_label(repository)}  weakest={weakest} overall={overall} "
                f"recon={state_label(repository.recon_done)} active={active}"
            )
    return "\n".join(lines)


def repository_status_text(payload: tuple[RepositoryStatus, ...]) -> str:
    lines: list[str] = []
    for index, status in enumerate(payload):
        if index:
            lines.append("")
        summary = status.audit.summary
        lines.extend(
            (
                f"repository: {repository_label(status.repository)}",
                f"current_head: {summary.current_head_sha or '-'}",
                f"audits: total={len(summary.items)} ready={len(summary.readable)} "
                f"active={len(summary.active)} stale={len(summary.stale)} failed={len(summary.failed)}",
            )
        )
        for item in summary.items:
            selector = item.audit_key.removeprefix("audit.")
            state = (
                item.task_lifecycle
                if item.active or item.task_lifecycle == "failed"
                else ("ready" if item.can_read else "missing")
            )
            lines.append(f"  {selector}  state={state} freshness={item.freshness.state}")
    return "\n".join(lines)


def audit_summary_text(payload: AuditSummary) -> str:
    lines = [f"repository: {payload.repo_id}"]
    for item in payload.audits:
        selector = item.audit_key.removeprefix("audit.")
        if item.available:
            score = "-" if item.score is None else f"{item.score:g}"
            lines.append(
                f"  {selector}  score={score} freshness={item.freshness.state} generated_at={item.generated_at or '-'}"
            )
        else:
            lines.append(f"  {selector}  unavailable={item.reason or 'unknown'} freshness={item.freshness.state}")
    return "\n".join(lines)


def audit_read_text(payload: AuditRead) -> str:
    return render_audit_read(payload)


def audit_wait_text(payload: AuditWaitResult) -> str:
    return f"repository: {payload.repo_id}\nstatus: {payload.status}\nreason: {payload.reason}\nelapsed_seconds: {payload.elapsed_seconds}"


def project_list_text(payload: tuple[ProjectRef, ...]) -> str:
    return "\n".join(f"{item.project_id}\t{item.name or '-'}" for item in payload) or "No projects found."


def project_settings_text(payload: ProjectSettings) -> str:
    lines = [
        f"project: {payload.project.name or payload.project.project_id}",
        f"language: {payload.account_preferences.language or '-'}",
    ]
    lines.extend(f"  {repository_label(item)}" for item in payload.repositories)
    return "\n".join(lines)


def _dimension(label: str, values: list[str], selectors: list[str]) -> str | None:
    if not values:
        return None
    unique = set(values)
    if unique == {"unset"}:
        return None
    if len(unique) == 1:
        return f"{label}={values[0]}"
    grouped = ",".join(f"{selector}={value}" for selector, value in zip(selectors, values, strict=True))
    return f"{label}=mixed[{grouped}]"


def _schedule_dimensions(listing: ScheduleListing, selectors: list[str]) -> str:
    items = listing.schedules
    dimensions = (
        _dimension("frequency", [item.cadence or "unset" for item in items], selectors),
        _dimension("timezone", [item.timezone or "unset" for item in items], selectors),
        _dimension(
            "enabled_state",
            ["unset" if item.enabled is None else str(item.enabled).lower() for item in items],
            selectors,
        ),
        _dimension("day", [item.schedule_day or "unset" for item in items], selectors),
        _dimension("day_of_month", [str(item.schedule_day_of_month or "unset") for item in items], selectors),
        _dimension("schedule_time", [item.schedule_time or "unset" for item in items], selectors),
        _dimension("schedule_time_source", [item.schedule_time_source or "unset" for item in items], selectors),
        _dimension("window_start", [item.window_start_time or "unset" for item in items], selectors),
        _dimension("window_end", [item.window_end_time or "unset" for item in items], selectors),
        _dimension("window_mode", [item.window_mode or "unset" for item in items], selectors),
    )
    return " ".join(item for item in dimensions if item is not None)


def _window_days_dimension(listing: ScheduleListing) -> str | None:
    restricted: dict[tuple[str, ...], list[str]] = {}
    for item in listing.schedules:
        restricted.setdefault(tuple(item.window_days), []).append(item.audit_key.removeprefix("audit."))
    if not any(days for days in restricted):
        return None
    groups = [
        f"{','.join(days) if days else 'unrestricted'}:{','.join(selectors)}" for days, selectors in restricted.items()
    ]
    return f"window_days={'|'.join(groups)}"


def schedule_text(payload: tuple[ScheduleListing, ...]) -> str:
    lines = []
    for listing in payload:
        selectors = [item.audit_key.removeprefix("audit.") for item in listing.schedules]
        schedules = listing.schedules
        enabled = sum(item.enabled is True for item in schedules)
        disabled = [item.audit_key.removeprefix("audit.") for item in schedules if item.enabled is False]
        fields = [f"enabled={enabled}/{len(schedules)}", _schedule_dimensions(listing, selectors)]
        if disabled:
            fields.append(f"disabled={','.join(disabled)}")
        if window_days := _window_days_dimension(listing):
            fields.append(window_days)
        lines.append(f"{repository_label(listing.repository)}  " + " ".join(field for field in fields if field))
    return "\n".join(lines) or "No schedules found."


def autofix_text(payload: tuple[AutofixListing, ...]) -> str:
    lines: list[str] = []
    for listing in payload:
        supported = [item for item in listing.items if item.definition.supported]
        configured = [item for item in supported if item.job is not None]
        enabled = sum(item.job is not None and item.job.enabled is True for item in supported)
        auto_fix = sum(item.job is not None and item.job.auto_fix is True for item in supported)
        selectors = ",".join(item.definition.selector for item in supported) or "-"
        configured_selectors = [item.definition.selector for item in configured]
        fields = [
            f"enabled={enabled}/{len(supported)}",
            f"configured={len(configured)}/{len(supported)}",
            f"auto_fix={auto_fix}/{len(supported)}",
            f"supported={selectors}",
            _autofix_dimensions(configured, configured_selectors),
        ]
        unconfigured = [item.definition.selector for item in supported if item.job is None]
        disabled = [item.definition.selector for item in configured if item.job and item.job.enabled is False]
        unknown = [item.definition.selector for item in configured if item.job and item.job.enabled is None]
        if unconfigured:
            fields.append(f"unconfigured={','.join(unconfigured)}")
        if disabled:
            fields.append(f"disabled={','.join(disabled)}")
        if unknown:
            fields.append(f"enabled_unknown={','.join(unknown)}")
        lines.append(f"{repository_label(listing.repository)}  " + " ".join(field for field in fields if field))
    return "\n".join(lines) or "No improvement jobs found."


def _autofix_dimensions(configured: list[AutofixListingItem], selectors: list[str]) -> str:
    dimensions = (
        _dimension(
            "enabled_state",
            ["unset" if item.job.enabled is None else str(item.job.enabled).lower() for item in configured if item.job],
            selectors,
        ),
        _dimension(
            "auto_fix_state",
            [
                "unset" if item.job.auto_fix is None else str(item.job.auto_fix).lower()
                for item in configured
                if item.job
            ],
            selectors,
        ),
        _dimension("frequency", [item.job.frequency or "unset" for item in configured if item.job], selectors),
        _dimension("timezone", [item.job.timezone or "unset" for item in configured if item.job], selectors),
        _dimension("days", [",".join(item.job.days_of_week) or "unset" for item in configured if item.job], selectors),
        _dimension("schedule_time", [item.job.schedule_time or "unset" for item in configured if item.job], selectors),
        _dimension(
            "schedule_time_source",
            [item.job.schedule_time_source or "unset" for item in configured if item.job],
            selectors,
        ),
        _dimension("pentest_mode", [item.job.pentest_mode or "unset" for item in configured if item.job], selectors),
    )
    return " ".join(item for item in dimensions if item is not None)


def gitlab_credentials_text(payload: GitLabCredentialsResult) -> str:
    lines = [
        f"scope: {payload.scope.scope_type or '-'}:{payload.scope.scope_owner or '-'}",
        f"credentials: {len(payload.credentials)} total={payload.pagination.total}",
    ]
    lines.extend(
        f"  {item.id}  {item.name}  provider={item.provider} status={item.status}" for item in payload.credentials
    )
    return "\n".join(lines)


def gitlab_projects_text(payload: GitLabProjectsResult) -> str:
    lines = [f"credential: {payload.credential.id} ({payload.credential.name})", f"projects: {len(payload.projects)}"]
    lines.extend(f"  {item.path_with_namespace}  {item.web_url or '-'}" for item in payload.projects)
    return "\n".join(lines)


def email_preferences_text(payload: object) -> str:
    rendered = json_projection(payload)
    if isinstance(rendered, (list, tuple)):
        return "\n".join(json.dumps(item, sort_keys=True) for item in rendered)
    return json.dumps(rendered, sort_keys=True)


_OPERATION_SELECTOR_FIELDS = ("audit_key", "action_key", "selector")
_OPERATION_RESERVED_FIELDS = frozenset(_OPERATION_SELECTOR_FIELDS)


def _operation_selector(item: Mapping[object, object]) -> object:
    for key in _OPERATION_SELECTOR_FIELDS:
        value = item.get(key)
        if value:
            return value
    return None


def _operation_details(item: Mapping[object, object]) -> str:
    return " ".join(f"{key}={value}" for key, value in item.items() if key not in _OPERATION_RESERVED_FIELDS)


def _operation_item_text(item: object) -> str:
    if not isinstance(item, dict):
        return str(item)
    selector = _operation_selector(item) or "item"
    details = _operation_details(item)
    return f"{selector}" + (f"  {details}" if details else "")


def _operation_field_text(key: object, value: object) -> str:
    rendered_value = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
    return f"{key}: {rendered_value}"


def _operation_mapping_text(rendered: dict[object, object]) -> str:
    lines = [_operation_field_text(key, value) for key, value in rendered.items() if key != "results"]
    results = rendered.get("results")
    if isinstance(results, list):
        lines.extend(["results:", *(f"  {_operation_item_text(item)}" for item in results)])
    return "\n".join(lines)


def _operation_sequence_text(rendered: list[object]) -> str:
    return "\n".join(_operation_item_text(item) for item in rendered)


def operation_text(payload: object) -> str:
    """Render mutation results as compact, operator-readable status lines."""
    rendered = json_projection(payload)
    if isinstance(rendered, dict):
        return _operation_mapping_text(rendered)
    if isinstance(rendered, list):
        return _operation_sequence_text(rendered)
    return str(rendered)


PORTFOLIO = CliPresentation(portfolio_text)
REPOSITORY_STATUS = CliPresentation(repository_status_text)
AUDIT_SUMMARY = CliPresentation(audit_summary_text)
AUDIT_READ = CliPresentation(audit_read_text)
AUDIT_WAIT = CliPresentation(audit_wait_text)
PROJECT_LIST = CliPresentation(project_list_text)
PROJECT_SETTINGS = CliPresentation(project_settings_text)
SCHEDULE = CliPresentation(schedule_text)
AUTOFIX = CliPresentation(autofix_text)
GITLAB_CREDENTIALS = CliPresentation(gitlab_credentials_text)
GITLAB_PROJECTS = CliPresentation(gitlab_projects_text)
EMAIL = CliPresentation(email_preferences_text)
OPERATION = CliPresentation(operation_text)
