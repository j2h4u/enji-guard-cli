"""Audit artifact freshness rules.

Freshness is deliberately represented separately from readability and task
lifecycle.  A stale artifact can still be useful, while an unreadable one is
not evidence at all.
"""

from enji_guard_cli.audit.ports import AuditFreshness, AuditFreshnessState, AuditItemStatus


def compare_heads(current_head_sha: str | None, audited_head_sha: str | None) -> AuditFreshness:
    if current_head_sha is None or audited_head_sha is None:
        state: AuditFreshnessState = "unknown"
    elif current_head_sha == audited_head_sha:
        state = "fresh"
    else:
        state = "stale"
    return AuditFreshness(current_head_sha, audited_head_sha, state)


def freshness_for_status(item: AuditItemStatus) -> AuditFreshness:
    return compare_heads(item.current_head_sha, item.audited_head_sha)


def stale(current_head_sha: str | None, audited_head_sha: str | None) -> bool | None:
    """Return the legacy-friendly tri-state result without hiding unknown."""

    return compare_heads(current_head_sha, audited_head_sha).stale


def stale_audits(status: tuple[AuditItemStatus, ...]) -> tuple[str, ...]:
    return tuple(item.action_key for item in status if freshness_for_status(item).state == "stale")


def aggregate_freshness(status: tuple[AuditItemStatus, ...]) -> AuditFreshnessState:
    states = {freshness_for_status(item).state for item in status}
    if states == {"fresh"}:
        return "fresh"
    if "stale" in states and len(states) == 1:
        return "stale"
    return "unknown"
