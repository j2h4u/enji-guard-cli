from enji_guard_cli.cli_impl.rendering_support import number_or_none, object_dict, object_list, string_or_none


def report_read_summary_payload(payload: object) -> dict[str, object]:
    data = object_dict(payload)
    summary: dict[str, object] = {
        "reports": [report_read_summary_item(item) for item in object_list(data.get("reports"))]
    }
    if "target" in data:
        summary["target"] = data["target"]
    return summary


def report_read_summary_item(item: object) -> dict[str, object]:
    report = object_dict(item)
    snapshot = object_dict(report.get("snapshot"))
    content = object_dict(snapshot.get("content"))
    summary_payload = object_dict(object_dict(content.get("summary")).get("summary"))
    available = report.get("available")
    if not isinstance(available, bool):
        available = bool(snapshot)
    return {
        "audit": report.get("audit"),
        "available": available,
        "score": number_or_none(summary_payload.get("score")),
        "headline": string_or_none(summary_payload.get("headline")),
        "completed_at": string_or_none(content.get("completedAt")) or string_or_none(snapshot.get("collectedAt")),
        "current_head_sha": report.get("current_head_sha"),
        "last_audited_head_sha": report.get("last_audited_head_sha"),
        "out_of_date": report.get("out_of_date"),
        "state": string_or_none(report.get("state")),
        "reason": string_or_none(report.get("reason")),
        "message": string_or_none(report.get("message")),
        "error_code": string_or_none(report.get("error_code")),
    }


def report_markdown(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("report payload is not an object")
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("report payload does not contain snapshot")
    content = snapshot.get("content")
    if not isinstance(content, dict):
        raise ValueError("report snapshot does not contain content")
    report = content.get("report")
    if not isinstance(report, str):
        raise ValueError("report snapshot does not contain markdown report")
    return report


def reports_markdown(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("reports payload is not an object")
    reports = payload.get("reports")
    if not isinstance(reports, list):
        raise ValueError("reports payload does not contain reports")
    parts = [report_item_markdown(item) for item in reports]
    return "\n\n---\n\n".join(parts)


def report_item_markdown(item: object) -> str:
    if not isinstance(item, dict):
        raise ValueError("report item is not an object")
    audit = item.get("audit")
    if not isinstance(audit, str):
        raise ValueError("report item does not contain audit")
    if item.get("available") is False:
        message = string_or_none(item.get("message")) or f"{audit} report is unavailable"
        return f"<!-- enji-report audit={audit} unavailable=true -->\n\n_{message}_"
    return f"<!-- enji-report audit={audit} -->\n\n{report_markdown(item).strip()}"
