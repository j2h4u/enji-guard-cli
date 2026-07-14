from collections.abc import Callable
from typing import Never

from enji_guard_cli.audits import AuditCatalog, AuditDefinition
from enji_guard_cli.core_impl.models import (
    ReportAuditStatusPayload,
    ReportReadItemPayload,
    ReportReadPayload,
    ReportReadState,
    ReportStatusPayload,
)
from enji_guard_cli.core_impl.payloads import json_dict
from enji_guard_cli.core_impl.repo_status import out_of_date
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.json_types import JsonObjectPayload

SnapshotReader = Callable[[str, AuditDefinition], JsonObjectPayload]


def selected_reports_to_read(
    status: ReportStatusPayload,
    audits: list[str],
    *,
    all_reports: bool,
    catalog: AuditCatalog,
) -> list[ReportAuditStatusPayload]:
    reports_by_audit = _report_status_by_audit(status)
    if all_reports:
        if audits:
            raise ValueError("pass report audits or --all, not both")
        return status["items"]
    if not audits:
        return [report for report in status["items"] if report["report"]["can_read"]]

    audits_by_selector = {audit.selector: audit for audit in catalog.published_audits}
    selected_reports: list[ReportAuditStatusPayload] = []
    for selector in audits:
        audit = audits_by_selector.get(selector)
        report = reports_by_audit.get(audit.action_key) if audit is not None else None
        if report is None:
            raise EnjiApiError("NOT_FOUND", f"{selector} report status not found")
        if not report["report"]["can_read"]:
            _raise_unreadable_report(report)
        selected_reports.append(report)
    return selected_reports


def _report_status_by_audit(status: ReportStatusPayload) -> dict[str, ReportAuditStatusPayload]:
    return {report["audit"]: report for report in status["items"]}


def read_reports_for_target(
    repo_id: str,
    reports: list[ReportAuditStatusPayload],
    *,
    snapshot_reader: SnapshotReader,
    tolerate_unavailable: bool,
) -> ReportReadPayload:
    return {
        "reports": [
            _report_read_item(
                repo_id,
                report,
                snapshot_reader=snapshot_reader,
                tolerate_unavailable=tolerate_unavailable,
            )
            for report in reports
        ]
    }


def _report_read_item(
    repo_id: str,
    report: ReportAuditStatusPayload,
    *,
    snapshot_reader: SnapshotReader,
    tolerate_unavailable: bool,
) -> ReportReadItemPayload:
    if not report["report"]["can_read"]:
        if tolerate_unavailable:
            return _unavailable_report_read_item(report, reason=_unreadable_report_reason(report))
        _raise_unreadable_report(report)

    audit = AuditDefinition(
        action_key=report["action_key"],
        title=report["label"],
        metric_group=report["metric_group"],
        runbook_kind="",
    )
    try:
        snapshot = json_dict(snapshot_reader(repo_id, audit).get("snapshot"))
    except EnjiApiError as exc:
        if exc.code == "NOT_FOUND" and tolerate_unavailable:
            return _unavailable_report_read_item(
                report,
                reason="snapshot_not_found",
                error_code=exc.code,
                message=f"{audit.action_key} snapshot not found",
            )
        if exc.code == "NOT_FOUND":
            raise EnjiApiError(exc.code, f"{audit.action_key} snapshot not found") from exc
        raise

    return _available_report_read_item(report, snapshot)


def _available_report_read_item(
    report: ReportAuditStatusPayload,
    snapshot: JsonObjectPayload,
) -> ReportReadItemPayload:
    report_status = report["report"]
    current_head_sha = report_status["current_head_sha"]
    last_audited_head_sha = report_status["audited_head_sha"]
    return {
        "audit": report["audit"],
        "current_head_sha": current_head_sha,
        "last_audited_head_sha": last_audited_head_sha,
        "out_of_date": report_status["stale"]
        if report_status["stale"] is not None
        else out_of_date(
            current_head_sha,
            last_audited_head_sha,
        ),
        "available": True,
        "state": "ready",
        "reason": None,
        "message": None,
        "snapshot": snapshot,
    }


def _unavailable_report_read_item(
    report: ReportAuditStatusPayload,
    *,
    reason: str,
    error_code: str | None = None,
    message: str | None = None,
) -> ReportReadItemPayload:
    report_status = report["report"]
    item: ReportReadItemPayload = {
        "audit": report["audit"],
        "current_head_sha": report_status["current_head_sha"],
        "last_audited_head_sha": report_status["audited_head_sha"],
        "out_of_date": report_status["stale"],
        "available": False,
        "state": _unreadable_report_state(reason),
        "reason": reason,
        "message": message or _unreadable_report_message(report),
    }
    if error_code is not None:
        item["error_code"] = error_code
    return item


def _unreadable_report_reason(report: ReportAuditStatusPayload) -> str:
    task_state = report["task"]["lifecycle_state"]
    if task_state == "queued":
        return "queued"
    if task_state == "running":
        return "running"
    if task_state == "failed":
        return "failed"
    return "missing"


def _unreadable_report_state(reason: str) -> ReportReadState:
    if reason in {"queued", "running"}:
        return "running"
    return "missing"


def _raise_unreadable_report(report: ReportAuditStatusPayload) -> Never:
    raise EnjiApiError("NOT_FOUND", _unreadable_report_message(report))


def _unreadable_report_message(report: ReportAuditStatusPayload) -> str:
    audit = report["audit"]
    state = report["task"]["lifecycle_state"]
    if state == "running":
        return f"{audit} report is still running"
    if state == "queued":
        return f"{audit} report is queued"
    if state == "failed":
        return f"{audit} report run failed"
    if report["report"]["readability_state"] == "unavailable":
        return f"{audit} report is missing"
    return f"{audit} report is not readable"
