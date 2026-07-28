"""Pure human-text presenters for Audit delivery DTOs."""

import re

from enji_guard_cli.application import AuditReadView

_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")
_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x0d\x0e-\x1f\x7f]")


def terminal_safe_report_body(body: str) -> str:
    """Strip terminal control sequences from upstream report Markdown."""

    return _UNSAFE_CONTROL_RE.sub("", _ANSI_ESCAPE_RE.sub("", body))


def render_audit_read(read: AuditReadView) -> str:
    """Render full audit artifacts as readable Markdown-oriented text."""

    sections = [f"repository: {read.repo_id}"]
    for item in read.audits:
        selector = item.audit_key.removeprefix("audit.")
        warning = "Report is stale; a newer audit is in progress." if item.newer_run else None
        if item.artifact is None:
            lines = [f"## {selector}"]
            if warning:
                lines.extend(("", warning))
            lines.extend(("", f"unavailable: {item.reason or 'unknown'}", f"freshness: {item.freshness.state}"))
            sections.append("\n".join(lines))
            continue
        metadata = [f"freshness: {item.freshness.state}"]
        if item.artifact.task_id is not None:
            metadata.append(f"task_id: {item.artifact.task_id}")
        if item.artifact.completed_at is not None:
            metadata.append(f"completed_at: {item.artifact.completed_at}")
        if item.artifact.collected_at is not None:
            metadata.append(f"collected_at: {item.artifact.collected_at}")
        if item.artifact.score is not None:
            metadata.append(f"score: {item.artifact.score:g}")
        if item.artifact.generated_at is not None:
            metadata.append(f"generated_at: {item.artifact.generated_at}")
        body = terminal_safe_report_body(item.artifact.body).strip()
        sections.append("\n".join((f"## {selector}", *([warning] if warning else []), *metadata, "", body)))
    return "\n\n".join(sections)
