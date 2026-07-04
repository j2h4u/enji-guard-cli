from enji_guard_cli.core_impl.models import ReportStatusPayload
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

SNAPSHOT_VISIBILITY_RISK_CODE = "SNAPSHOT_VISIBILITY_RISK"
SNAPSHOT_VISIBILITY_RISK_MESSAGE = "starting report audits can temporarily hide older snapshots"


def report_start_preflight_payload(status: ReportStatusPayload) -> JsonObjectPayload:
    stale = [report["audit"] for report in status["reports"] if report["out_of_date"] is True]
    counts: JsonObjectPayload = {
        "ready": len(status["ready"]),
        "running": len(status["running"]),
        "stale": len(stale),
    }
    lists: JsonObjectPayload = {
        "ready": list[JsonValue](status["ready"]),
        "running": list[JsonValue](status["running"]),
        "stale": list[JsonValue](stale),
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
