from enji_guard_cli.audit.freshness import compare_heads, stale


def test_freshness_keeps_unknown_sha_explicit() -> None:
    assert compare_heads(None, "abc").state == "unknown"
    assert stale(None, "abc") is None
