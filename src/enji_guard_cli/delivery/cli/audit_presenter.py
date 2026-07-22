"""Pure human-text presenters for Audit delivery DTOs."""

from enji_guard_cli.application import AuditRead


def render_audit_read(read: AuditRead) -> str:
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
        sections.append(
            "\n".join((f"## {selector}", *([warning] if warning else []), *metadata, "", item.artifact.body.strip()))
        )
    return "\n\n".join(sections)
