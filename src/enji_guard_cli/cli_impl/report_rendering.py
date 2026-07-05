import re

import typer

from enji_guard_cli.cli_impl.rendering import echo_table
from enji_guard_cli.cli_impl.rendering_support import number_or_none, object_dict, object_list, string_or_none

_ANSI_CSI_RE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"(?:\x1b\]|\x9d).*?(?:\x07|\x1b\\)", re.DOTALL)
_CONTROL_CHARS = dict.fromkeys(
    codepoint for codepoint in (*range(0x09), *range(0x0B, 0x0D), *range(0x0E, 0x20), *range(0x7F, 0xA0))
)


def report_summary_payload(payload: object) -> dict[str, object]:
    data = object_dict(payload)
    summary: dict[str, object] = {"reports": [report_summary_item(item) for item in object_list(data.get("reports"))]}
    if "target" in data:
        summary["target"] = data["target"]
    return summary


def report_summary_item(item: object) -> dict[str, object]:
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


def echo_report_summary(payload: object) -> None:
    reports = [object_dict(item) for item in object_list(report_summary_payload(payload).get("reports"))]
    rows = [report_summary_row(report) for report in reports]
    echo_table(
        ("audit", "available", "score", "freshness", "completed_at", "headline"),
        rows,
        "No reports.",
    )
    for report in reports:
        if report.get("available") is False:
            audit = string_or_none(report.get("audit")) or "-"
            reason = string_or_none(report.get("reason")) or "unavailable"
            message = string_or_none(report.get("message")) or f"{audit} report is unavailable"
            typer.echo(f"{audit}: {reason}: {message}")


def report_summary_row(report: dict[str, object]) -> tuple[str, ...]:
    freshness = "unknown"
    if report.get("available") is False:
        freshness = string_or_none(report.get("reason")) or "unavailable"
    elif report.get("out_of_date") is True:
        freshness = "stale"
    elif report.get("out_of_date") is False:
        freshness = "fresh"
    return (
        text_cell(report.get("audit")),
        text_cell(report.get("available")),
        score_cell(report.get("score")),
        freshness,
        text_cell(report.get("completed_at")),
        text_cell(report.get("headline")),
    )


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


def safe_terminal_markdown(markdown: str) -> str:
    without_osc = _ANSI_OSC_RE.sub("", markdown)
    without_csi = _ANSI_CSI_RE.sub("", without_osc)
    return without_csi.translate(_CONTROL_CHARS)


def report_item_markdown(item: object) -> str:
    if not isinstance(item, dict):
        raise ValueError("report item is not an object")
    audit = item.get("audit")
    if not isinstance(audit, str):
        raise ValueError("report item does not contain audit")
    if item.get("available") is False:
        message = string_or_none(item.get("message")) or f"{audit} report is unavailable"
        return f"<!-- enji-report audit={audit} unavailable=true -->\n\n_{safe_terminal_markdown(message)}_"
    return f"<!-- enji-report audit={audit} -->\n\n{safe_terminal_markdown(report_markdown(item)).strip()}"


def text_cell(value: object, *, fallback: str = "-") -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def score_cell(value: object) -> str:
    score = number_or_none(value)
    if score is None:
        return "-"
    if isinstance(score, int):
        return str(score)
    return f"{score:.0f}" if score.is_integer() else f"{score:.1f}"
