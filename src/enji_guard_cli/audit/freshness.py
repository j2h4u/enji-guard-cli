"""Audit artifact freshness rules.

Freshness is deliberately represented separately from readability and task
lifecycle.  A stale artifact can still be useful, while an unreadable one is
not evidence at all.
"""

from enji_guard_cli.audit.ports import AuditFreshness, AuditFreshnessState


def compare_heads(current_head_sha: str | None, audited_head_sha: str | None) -> AuditFreshness:
    if current_head_sha is None or audited_head_sha is None:
        state: AuditFreshnessState = "unknown"
    elif current_head_sha == audited_head_sha:
        state = "fresh"
    else:
        state = "stale"
    return AuditFreshness(current_head_sha, audited_head_sha, state)


def stale(current_head_sha: str | None, audited_head_sha: str | None) -> bool | None:
    """Return the tri-state result without hiding unknown freshness."""

    return compare_heads(current_head_sha, audited_head_sha).stale
