from enji_guard_cli.core_impl.models import ReportAuditStatusPayload, ReportStatusPayload
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

SNAPSHOT_VISIBILITY_RISK_CODE = "SNAPSHOT_VISIBILITY_RISK"
SNAPSHOT_VISIBILITY_RISK_MESSAGE = "starting report audits can temporarily hide older snapshots"


def report_start_preflight_payload(status: ReportStatusPayload) -> JsonObjectPayload:
    readable = [report["audit"] for report in status["items"] if report["report"]["can_read"]]
    active = [report["audit"] for report in status["items"] if report["task"]["active"]]
    queued = _audits_with_task_lifecycle(status["items"], "queued")
    running = _audits_with_task_lifecycle(status["items"], "running")
    stale = [report["audit"] for report in status["items"] if report["report"]["stale"] is True]
    missing = [report["audit"] for report in status["items"] if not report["report"]["can_read"]]
    counts: JsonObjectPayload = {
        "readable": len(readable),
        "active": len(active),
        "queued": len(queued),
        "running": len(running),
        "stale": len(stale),
        "ready": len(readable),
        "missing": len(missing),
    }
    lists: JsonObjectPayload = {
        "readable": list[JsonValue](readable),
        "active": list[JsonValue](active),
        "queued": list[JsonValue](queued),
        "running": list[JsonValue](running),
        "stale": list[JsonValue](stale),
        "ready": list[JsonValue](readable),
        "missing": list[JsonValue](missing),
    }
    return {
        "warning": {
            "code": SNAPSHOT_VISIBILITY_RISK_CODE,
            "message": SNAPSHOT_VISIBILITY_RISK_MESSAGE,
        },
        "counts": counts,
        "lists": lists,
        "current_head_sha": status["current_head_sha"],
        "last_report_at": status["last_report_at"],
    }


def _audits_with_task_lifecycle(
    reports: list[ReportAuditStatusPayload],
    lifecycle_state: str,
) -> list[str]:
    return [report["audit"] for report in reports if report["task"]["lifecycle_state"] == lifecycle_state]
