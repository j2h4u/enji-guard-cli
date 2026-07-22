"""Pure human-text presenters for Audit delivery DTOs."""

from enji_guard_cli.application import AuditRead


def render_audit_read(read: AuditRead) -> str:
    """Render full audit artifacts as readable Markdown-oriented text."""

    sections = [f"repository: {read.repo_id}"]
    for item in read.audits:
        selector = item.audit_key.removeprefix("audit.")
        if item.artifact is None:
            sections.append(
                f"## {selector}\n\nunavailable: {item.reason or 'unknown'}\nfreshness: {item.freshness.state}"
            )
            continue
        metadata = [f"freshness: {item.freshness.state}"]
        if item.artifact.score is not None:
            metadata.append(f"score: {item.artifact.score:g}")
        if item.artifact.generated_at is not None:
            metadata.append(f"generated_at: {item.artifact.generated_at}")
        sections.append("\n".join((f"## {selector}", *metadata, "", item.artifact.body.strip())))
    return "\n\n".join(sections)
