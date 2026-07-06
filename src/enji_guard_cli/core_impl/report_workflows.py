from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from enji_guard_cli.audits import AuditAlias, ReportAuditDefinition
from enji_guard_cli.core_impl import active_run_ledger as _active_run_ledger
from enji_guard_cli.core_impl import audit_runs as _audit_runs
from enji_guard_cli.core_impl import report_reads as _report_reads
from enji_guard_cli.core_impl import report_wait as _report_wait
from enji_guard_cli.core_impl.models import (
    AuditRunBatchPayload,
    ReportStatusPayload,
    ReportWaitCallback,
    ReportWaitOptions,
    ReportWaitPayload,
    RepoTargetPayload,
)
from enji_guard_cli.core_impl.payloads import json_dict as _json_dict
from enji_guard_cli.core_impl.payloads import json_object_payload as _json_object_payload
from enji_guard_cli.core_impl.payloads import json_str as _json_str
from enji_guard_cli.core_impl.preflight import report_start_preflight_payload as _report_start_preflight_payload
from enji_guard_cli.core_impl.repo_status import current_active_runs as _current_active_runs
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue
from enji_guard_cli.settings import ActiveRunLedgerSettings

type ResolveSingleRepoTarget = Callable[[str, str | None], RepoTargetPayload]
type TargetedRunPayload = Callable[[RepoTargetPayload, object], dict[str, object]]
type RequireReportAudit = Callable[[AuditAlias], ReportAuditDefinition]
type ReportSnapshot = Callable[[str, str], JsonObjectPayload]
type StartReportAuditsForTarget = Callable[[str, str, list[AuditAlias]], AuditRunBatchPayload]
type WaitForReportCompletion = Callable[..., ReportWaitPayload]
type ActiveRunLedgerSettingsProvider = Callable[[], ActiveRunLedgerSettings]
type NowUtc = Callable[[], datetime]
type StartAuditDependenciesFactory[TCreateRequest] = Callable[[], _audit_runs.StartAuditDependencies[TCreateRequest]]


@dataclass(frozen=True, slots=True)
class ReportWorkflowDependencies[TCreateRequest]:
    list_repo_active_runs: Callable[[str], JsonObjectPayload]
    get_repo_rerun_state: Callable[[str], JsonObjectPayload]
    list_repo_task_links: Callable[[str], JsonObjectPayload]
    report_status_from_task_links: Callable[
        [str, JsonObjectPayload, list[JsonValue], JsonObjectPayload],
        ReportStatusPayload,
    ]
    resolve_single_repo_target: ResolveSingleRepoTarget
    targeted_run_payload: TargetedRunPayload
    report_status: Callable[[str], ReportStatusPayload]
    report_snapshot: ReportSnapshot
    read_report_snapshot: Callable[[str, AuditAlias], JsonObjectPayload]
    wait_for_report_completion: WaitForReportCompletion
    start_report_audits_for_target: StartReportAuditsForTarget
    require_report_audit: RequireReportAudit
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]
    get_task: Callable[[str], JsonObjectPayload]
    active_run_ledger_settings: ActiveRunLedgerSettingsProvider
    now_utc: NowUtc
    start_audit_dependencies: StartAuditDependenciesFactory[TCreateRequest]


def list_repo_active_runs[TCreateRequest](
    repo_id: str,
    *,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> JsonObjectPayload:
    return dependencies.list_repo_active_runs(repo_id)


def get_repo_rerun_state[TCreateRequest](
    repo_id: str,
    *,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> JsonObjectPayload:
    return dependencies.get_repo_rerun_state(repo_id)


def list_repo_task_links[TCreateRequest](
    repo_id: str,
    *,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> JsonObjectPayload:
    return dependencies.list_repo_task_links(repo_id)


def report_status[TCreateRequest](
    repo_id: str,
    *,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> ReportStatusPayload:
    rerun_state = dependencies.get_repo_rerun_state(repo_id)
    task_links = dependencies.list_repo_task_links(repo_id)
    active_runs = merged_repo_active_runs(
        repo_id,
        rerun_state=rerun_state,
        task_links=task_links,
        dependencies=dependencies,
    )
    return dependencies.report_status_from_task_links(repo_id, task_links, active_runs, rerun_state)


def wait_for_report_completion[TCreateRequest](
    repo_id: str,
    *,
    options: ReportWaitOptions,
    heartbeat: Callable[[ReportWaitPayload], None] | None,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> ReportWaitPayload:
    return _report_wait.wait_for_report_completion(
        repo_id,
        options=options,
        heartbeat=heartbeat,
        dependencies=_report_wait.ReportWaitDependencies(
            read_status=dependencies.report_status,
            monotonic=dependencies.monotonic,
            sleep=dependencies.sleep,
        ),
    )


def start_report_audits[TCreateRequest](
    repo: str,
    project: str | None,
    audits: list[AuditAlias],
    *,
    all_reports: bool,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> dict[str, object]:
    target = dependencies.resolve_single_repo_target(repo, project)
    selected_audits = selected_report_audits(audits, all_reports=all_reports, dependencies=dependencies)
    status = dependencies.report_status(target["repo_id"])
    preflight = _report_start_preflight_payload(status)
    linked_running_results = _audit_runs.linked_running_report_results(status, selected_audits)
    remaining_audits = [
        audit
        for audit in selected_audits
        if dependencies.require_report_audit(audit).action_key not in linked_running_results
    ]
    started_results = dependencies.start_report_audits_for_target(
        target["repo_id"], target["project_id"], remaining_audits
    )
    return dependencies.targeted_run_payload(
        target,
        {
            "preflight": preflight,
            "results": _audit_runs.ordered_audit_results(
                selected_audits, linked_running_results, started_results["results"]
            ),
        },
    )


def start_report_audits_for_target[TCreateRequest](
    repo_id: str,
    project_id: str,
    audits: list[AuditAlias],
    *,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> AuditRunBatchPayload:
    return _audit_runs.start_report_audits_for_target(
        repo_id,
        project_id,
        audits,
        dependencies=dependencies.start_audit_dependencies(),
        get_repo_rerun_state=dependencies.get_repo_rerun_state,
    )


def read_reports_for_repo[TCreateRequest](
    repo: str,
    project: str | None,
    audits: list[AuditAlias],
    *,
    all_reports: bool,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> dict[str, object]:
    target = dependencies.resolve_single_repo_target(repo, project)
    status = dependencies.report_status(target["repo_id"])
    selected_reports = _report_reads.selected_reports_to_read(status, audits, all_reports=all_reports)
    return dependencies.targeted_run_payload(
        target,
        _report_reads.read_reports_for_target(
            target["repo_id"],
            selected_reports,
            snapshot_reader=dependencies.read_report_snapshot,
            tolerate_unavailable=not audits,
        ),
    )


def read_report_snapshot[TCreateRequest](
    repo_id: str,
    audit: AuditAlias,
    *,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> JsonObjectPayload:
    resolved: ReportAuditDefinition = dependencies.require_report_audit(audit)
    return dependencies.report_snapshot(repo_id, resolved.route_slug)


def wait_for_reports[TCreateRequest](
    repo: str,
    project: str | None,
    *,
    options: ReportWaitOptions,
    heartbeat: ReportWaitCallback | None,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> dict[str, object]:
    target = dependencies.resolve_single_repo_target(repo, project)
    targeted_heartbeat: Callable[[ReportWaitPayload], None] | None = None
    if heartbeat is not None:

        def target_heartbeat(payload: ReportWaitPayload) -> None:
            heartbeat(dependencies.targeted_run_payload(target, payload))

        targeted_heartbeat = target_heartbeat

    payload = dependencies.wait_for_report_completion(
        target["repo_id"],
        options=options,
        heartbeat=targeted_heartbeat,
    )
    return dependencies.targeted_run_payload(target, payload)


def selected_report_audits[TCreateRequest](
    audits: list[AuditAlias],
    *,
    all_reports: bool,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> list[AuditAlias]:
    del dependencies
    return _audit_runs.selected_report_audits(audits, all_reports=all_reports)


def merged_repo_active_runs[TCreateRequest](
    repo_id: str,
    *,
    rerun_state: JsonObjectPayload | None = None,
    task_links: JsonObjectPayload | None = None,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> list[JsonValue]:
    ledger_settings = dependencies.active_run_ledger_settings()
    upstream_active_runs = _current_active_runs(dependencies.list_repo_active_runs(repo_id))
    if _active_run_ledger.has_entries_for_repo(ledger_settings, repo_id) is False:
        return upstream_active_runs
    try:
        resolved_rerun_state = rerun_state if rerun_state is not None else dependencies.get_repo_rerun_state(repo_id)
        resolved_task_links = task_links if task_links is not None else dependencies.list_repo_task_links(repo_id)
    except EnjiApiError:
        if upstream_active_runs:
            return upstream_active_runs
        resolved_rerun_state = None
        resolved_task_links = cast(JsonObjectPayload, {"links": []})
    return _active_run_ledger.merged_active_runs(
        repo_id,
        upstream_active_runs,
        resolved_rerun_state,
        resolved_task_links,
        get_task=dependencies.get_task,
        settings=ledger_settings,
        now=dependencies.now_utc(),
    )


def record_started_run[TCreateRequest](
    context: _audit_runs.RecordStartedRunContext,
    *,
    dependencies: ReportWorkflowDependencies[TCreateRequest],
) -> None:
    normalized = _json_object_payload(context.response)
    task_payload = _json_dict(normalized.get("task"))
    ledger_settings = dependencies.active_run_ledger_settings()
    _active_run_ledger.record_started_run(
        ledger_settings,
        _active_run_ledger.new_entry(
            repo_id=context.repo_id,
            project_id=context.project_id,
            action_key=context.action_key,
            task_id=_json_str(normalized.get("id")) or _json_str(task_payload.get("id")),
            task_status=_json_str(normalized.get("status")) or _json_str(task_payload.get("status")),
            current_head_sha=context.current_head_sha,
            last_audited_head_sha=context.last_audited_head_sha,
            observed_at=dependencies.now_utc(),
            started_at=None,
            ttl_seconds=ledger_settings.ttl_seconds,
        ),
    )
