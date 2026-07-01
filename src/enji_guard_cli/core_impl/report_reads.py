from collections.abc import Callable
from typing import Never

from enji_guard_cli.audits import AuditAlias
from enji_guard_cli.audits import require_report_audit as registry_require_report_audit
from enji_guard_cli.core_impl.models import (
    ReportAuditStatusPayload,
    ReportReadItemPayload,
    ReportReadPayload,
    ReportStatusPayload,
)
from enji_guard_cli.core_impl.payloads import json_dict
from enji_guard_cli.core_impl.repo_status import out_of_date
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.json_types import JsonObjectPayload

SnapshotReader = Callable[[str, AuditAlias], JsonObjectPayload]


def selected_reports_to_read(
    status: ReportStatusPayload,
    audits: list[AuditAlias],
    *,
    all_reports: bool,
) -> list[ReportAuditStatusPayload]:
    reports_by_audit = _report_status_by_audit(status)
    if all_reports:
        if audits:
            raise ValueError("pass report audits or --all, not both")
        return status["reports"]
    if not audits:
        return [report for report in status["reports"] if report["ready"]]

    selected_reports: list[ReportAuditStatusPayload] = []
    for audit in audits:
        registry_require_report_audit(audit)
        report = reports_by_audit.get(audit.value)
        if report is None:
            raise EnjiApiError("NOT_FOUND", f"{audit.value} report status not found")
        if not report["ready"]:
            _raise_unreadable_report(report)
        selected_reports.append(report)
    return selected_reports


def _report_status_by_audit(status: ReportStatusPayload) -> dict[str, ReportAuditStatusPayload]:
    return {report["audit"]: report for report in status["reports"]}


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
    if not report["ready"]:
        if tolerate_unavailable:
            return _unavailable_report_read_item(report, reason=report["state"])
        _raise_unreadable_report(report)

    audit = AuditAlias(report["audit"])
    try:
        snapshot = json_dict(snapshot_reader(repo_id, audit).get("snapshot"))
    except EnjiApiError as exc:
        if exc.code == "NOT_FOUND" and tolerate_unavailable:
            return _unavailable_report_read_item(
                report,
                reason="snapshot_not_found",
                error_code=exc.code,
                message=f"{audit.value} snapshot not found",
            )
        if exc.code == "NOT_FOUND":
            raise EnjiApiError(exc.code, f"{audit.value} snapshot not found") from exc
        raise

    return _available_report_read_item(report, snapshot)


def _available_report_read_item(
    report: ReportAuditStatusPayload,
    snapshot: JsonObjectPayload,
) -> ReportReadItemPayload:
    current_head_sha = report["current_head_sha"]
    last_audited_head_sha = report["last_audited_head_sha"]
    return {
        "audit": report["audit"],
        "current_head_sha": current_head_sha,
        "last_audited_head_sha": last_audited_head_sha,
        "out_of_date": report["out_of_date"]
        if report["out_of_date"] is not None
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
    item: ReportReadItemPayload = {
        "audit": report["audit"],
        "current_head_sha": report["current_head_sha"],
        "last_audited_head_sha": report["last_audited_head_sha"],
        "out_of_date": report["out_of_date"],
        "available": False,
        "state": report["state"],
        "reason": reason,
        "message": message or _unreadable_report_message(report),
    }
    if error_code is not None:
        item["error_code"] = error_code
    return item


def _raise_unreadable_report(report: ReportAuditStatusPayload) -> Never:
    raise EnjiApiError("NOT_FOUND", _unreadable_report_message(report))


def _unreadable_report_message(report: ReportAuditStatusPayload) -> str:
    audit = report["audit"]
    state = report["state"]
    if state == "missing":
        return f"{audit} report is missing"
    if state == "running":
        return f"{audit} report is still running"
    return f"{audit} report is not readable"
