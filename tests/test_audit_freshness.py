from enji_guard_cli.audit.freshness import aggregate_freshness, compare_heads, stale
from enji_guard_cli.audit.ports import AuditItemStatus


def test_freshness_keeps_unknown_sha_explicit() -> None:
    assert compare_heads(None, "abc").state == "unknown"
    assert stale(None, "abc") is None


def test_aggregate_freshness_is_stale_when_all_ready_artifacts_are_stale() -> None:
    status = (
        AuditItemStatus("audit.security", "new", "old", True, None, None, None, False),
        AuditItemStatus("audit.tests", "new", "old", True, None, None, None, False),
    )
    assert aggregate_freshness(status) == "stale"
