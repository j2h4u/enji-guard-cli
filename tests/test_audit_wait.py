from enji_guard_cli.audit.ports import AuditFreshness, AuditStatus, AuditStatusItem, AuditWaitOptions
from enji_guard_cli.audit.wait import AuditWaitDependencies, wait_for_completion


def test_wait_returns_ready_status_without_polling_barrier() -> None:
    status = AuditStatus(
        "repo",
        "sha",
        (
            AuditStatusItem(
                "audit.security", "Security", AuditFreshness("sha", "sha", "fresh"), True, "none", None, None
            ),
        ),
    )
    result = wait_for_completion(
        "repo",
        options=AuditWaitOptions(30, 100, 60),
        dependencies=AuditWaitDependencies(lambda _repo: status, lambda: 0.0, lambda _seconds: None),
    )
    assert result.complete is True
    assert result.reason == "complete"
