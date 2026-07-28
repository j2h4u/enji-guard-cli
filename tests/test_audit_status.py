import pytest

from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import AuditRerunState, AuditRun, AuditTaskLink
from enji_guard_cli.audit.status import build_status


def _catalog() -> AuditCatalog:
    return AuditCatalog(
        published_audits=(AuditDefinition("audit.security", "Security", "vulns", "audit"),),
        recon=AuditDefinition("audit.recon", "Recon", None, "recon"),
    )


def test_partial_link_with_active_run_is_not_readable() -> None:
    catalog = AuditCatalog(
        published_audits=(AuditDefinition("audit.security", "Security", "vulns", "audit"),),
        recon=AuditDefinition("audit.recon", "Recon", None, "recon"),
    )
    status = build_status(
        "repo-1",
        catalog,
        (AuditTaskLink("task-1", "audit.security", None),),
        (AuditRun("task-1", "audit.security", "running", None, None, None),),
        AuditRerunState(None, None, None, None),
    )

    assert status.items[0].task_lifecycle == "running"
    assert status.items[0].can_read is False


def test_partial_link_without_active_run_does_not_invent_activity() -> None:
    catalog = AuditCatalog(
        published_audits=(AuditDefinition("audit.security", "Security", "vulns", "audit"),),
        recon=AuditDefinition("audit.recon", "Recon", None, "recon"),
    )
    status = build_status(
        "repo-1",
        catalog,
        (AuditTaskLink("stale", "audit.security", "running", started_at="2026-01-01T00:00:00+00:00"),),
        (),
        AuditRerunState(None, None, None, None),
    )

    assert status.items[0].task_lifecycle == "none"
    assert status.items[0].task_id is None
    assert status.items[0].can_read is False


def test_active_run_owns_identity_over_conflicting_link() -> None:
    catalog = AuditCatalog(
        published_audits=(AuditDefinition("audit.security", "Security", "vulns", "audit"),),
        recon=AuditDefinition("audit.recon", "Recon", None, "recon"),
    )
    status = build_status(
        "repo-1",
        catalog,
        (AuditTaskLink("stale", "audit.security", "running", started_at="2026-01-01T00:00:00+00:00"),),
        (AuditRun("current", "audit.security", "queued", None, None, None),),
        AuditRerunState(None, None, None, None),
    )

    assert status.items[0].task_lifecycle == "queued"
    assert status.items[0].task_id == "current"
    assert status.items[0].started_at is None


def test_terminal_link_remains_readable_without_active_run() -> None:
    catalog = AuditCatalog(
        published_audits=(AuditDefinition("audit.security", "Security", "vulns", "audit"),),
        recon=AuditDefinition("audit.recon", "Recon", None, "recon"),
    )
    status = build_status(
        "repo-1",
        catalog,
        (AuditTaskLink("done", "audit.security", "completed", completed_at="2026-01-01T00:00:00+00:00"),),
        (),
        AuditRerunState(None, None, None, None, {"audit.security": "audited-sha"}),
    )

    assert status.items[0].task_lifecycle == "completed"
    assert status.items[0].task_id == "done"
    assert status.items[0].can_read is True


def test_audited_result_remains_readable_when_task_link_history_is_empty() -> None:
    status = build_status(
        "repo-1",
        _catalog(),
        (),
        (),
        AuditRerunState("current-sha", None, None, None, {"audit.security": "current-sha"}),
    )

    assert status.items[0].task_lifecycle == "none"
    assert status.items[0].can_read is True
    assert status.items[0].freshness.state == "fresh"
    assert status.items[0].current_head.state == "ready"
    assert status.items[0].current_head.action_required == "none"


def test_stale_readable_report_with_current_head_run_is_not_ambiguous_ready() -> None:
    status = build_status(
        "repo-1",
        _catalog(),
        (),
        (
            AuditRun(
                "current-run",
                "audit.security",
                "in_progress",
                "2026-01-01T00:03:00+00:00",
                None,
                None,
                current_head_sha="current-sha",
                last_audited_head_sha="old-sha",
            ),
        ),
        AuditRerunState("current-sha", None, None, None, {"audit.security": "old-sha"}),
    )

    item = status.items[0]
    assert item.can_read is True
    assert item.freshness.state == "stale"
    assert item.task_lifecycle == "running"
    assert item.current_head.state == "running"
    assert item.current_head.action_required == "wait_for_current_head_run"
    assert item.current_head.task_id == "current-run"


def test_stale_report_with_only_old_active_run_requires_current_head_start() -> None:
    status = build_status(
        "repo-1",
        _catalog(),
        (),
        (
            AuditRun(
                "old-run",
                "audit.security",
                "in_progress",
                "2026-01-01T00:03:00+00:00",
                None,
                None,
                current_head_sha="old-sha",
            ),
        ),
        AuditRerunState("current-sha", None, None, None, {"audit.security": "old-sha"}),
    )

    item = status.items[0]
    assert item.can_read is True
    assert item.freshness.state == "stale"
    assert item.current_head.state == "blocked"
    assert item.current_head.action_required == "start_current_head_run"
    assert item.current_head.stale_active_task_id == "old-run"
    assert item.current_head.stale_active_current_head_sha == "old-sha"


def test_current_head_run_wins_over_stale_running_run_for_readiness() -> None:
    status = build_status(
        "repo-1",
        _catalog(),
        (),
        (
            AuditRun(
                "old-run",
                "audit.security",
                "in_progress",
                "2026-01-01T00:03:00+00:00",
                None,
                None,
                current_head_sha="old-sha",
            ),
            AuditRun(
                "current-run",
                "audit.security",
                "pending",
                "2026-01-01T00:01:00+00:00",
                None,
                None,
                current_head_sha="current-sha",
            ),
        ),
        AuditRerunState("current-sha", None, None, None, {"audit.security": "old-sha"}),
    )

    item = status.items[0]
    assert item.can_read is True
    assert item.freshness.state == "stale"
    assert item.task_id == "current-run"
    assert item.task_lifecycle == "queued"
    assert item.current_head.state == "queued"
    assert item.current_head.action_required == "wait_for_current_head_run"
    assert item.current_head.task_id == "current-run"
    assert item.current_head.task_current_head_sha == "current-sha"


@pytest.mark.parametrize(
    "links",
    [
        (
            AuditTaskLink("done", "audit.security", "completed", completed_at="2026-01-01T00:02:00+00:00"),
            AuditTaskLink("running", "audit.security", "running", started_at="2026-01-01T00:03:00+00:00"),
        ),
        (
            AuditTaskLink("running", "audit.security", "running", started_at="2026-01-01T00:03:00+00:00"),
            AuditTaskLink("done", "audit.security", "completed", completed_at="2026-01-01T00:02:00+00:00"),
        ),
    ],
)
def test_terminal_link_history_wins_over_nonterminal_link_in_both_orders(
    links: tuple[AuditTaskLink, AuditTaskLink],
) -> None:
    status = build_status("repo-1", _catalog(), links, (), AuditRerunState(None, None, None, None))
    assert status.items[0].task_lifecycle == "completed"
    assert status.items[0].task_id == "done"
    assert status.items[0].can_read is True


@pytest.mark.parametrize(
    "runs",
    [
        (
            AuditRun("done", "audit.security", "completed", None, None, "2026-01-01T00:02:00+00:00"),
            AuditRun("current", "audit.security", "queued", None, None, None),
        ),
        (
            AuditRun("current", "audit.security", "queued", None, None, None),
            AuditRun("done", "audit.security", "completed", None, None, "2026-01-01T00:02:00+00:00"),
        ),
    ],
)
def test_terminal_active_run_history_never_overrides_active_run_in_both_orders(
    runs: tuple[AuditRun, AuditRun],
) -> None:
    status = build_status("repo-1", _catalog(), (), runs, AuditRerunState(None, None, None, None))
    assert status.items[0].task_lifecycle == "queued"
    assert status.items[0].task_id == "current"


def test_pending_run_without_head_evidence_is_not_presented_as_worth_waiting_for() -> None:
    """Reported defect: a pending task carrying no current-head metadata was
    reported as ``queued``/``wait_for_current_head_run``, i.e. absence of
    evidence rendered as evidence of currency."""

    status = build_status(
        "repo-1",
        _catalog(),
        (),
        (
            AuditRun(
                "3265ad0f-0000-4000-8000-000000000000",
                "audit.security",
                "pending",
                "2026-07-25T20:17:17.698Z",
                None,
                None,
            ),
        ),
        AuditRerunState("current-sha", None, None, None, {"audit.security": "old-sha"}),
    )

    item = status.items[0]
    assert item.freshness.state == "stale"
    assert item.current_head.state == "unverified"
    assert item.current_head.action_required == "inspect_unverified_run"
    assert item.current_head.action_required != "wait_for_current_head_run"
    assert item.current_head.task_id == "3265ad0f-0000-4000-8000-000000000000"
    assert item.current_head.task_status == "pending"
    assert item.current_head.task_current_head_sha is None


def test_running_run_without_head_evidence_is_unverified_too() -> None:
    status = build_status(
        "repo-1",
        _catalog(),
        (),
        (
            AuditRun(
                "no-evidence",
                "audit.security",
                "in_progress",
                "2026-07-25T20:17:17.698Z",
                "2026-07-25T20:18:00.000Z",
                None,
            ),
        ),
        AuditRerunState("current-sha", None, None, None, {"audit.security": "old-sha"}),
    )

    item = status.items[0]
    assert item.current_head.state == "unverified"
    assert item.current_head.action_required == "inspect_unverified_run"


def test_failed_run_without_head_evidence_is_not_reported_as_unverified() -> None:
    """Only live runs can be unproved.  A failed run is not an active run at
    all, so the current-head answer stays "nothing is running for this head"."""

    status = build_status(
        "repo-1",
        _catalog(),
        (),
        (AuditRun("boom", "audit.security", "failed", "2026-07-25T20:17:17.698Z", None, None),),
        AuditRerunState("current-sha", None, None, None, {"audit.security": "old-sha"}),
    )

    item = status.items[0]
    assert item.current_head.state == "missing"
    assert item.current_head.action_required == "start_current_head_run"


def test_unknown_current_head_is_its_own_state_not_unverified() -> None:
    """An unproved run and an unknown repository head are different problems:
    the first is about the task, the second about the repository."""

    status = build_status(
        "repo-1",
        _catalog(),
        (),
        (AuditRun("pending-run", "audit.security", "pending", "2026-07-25T20:17:17.698Z", None, None),),
        AuditRerunState(None, None, None, None, {"audit.security": "old-sha"}),
    )

    item = status.items[0]
    assert item.current_head.state == "unknown"
    assert item.current_head.action_required == "resolve_unknown_head"


def test_unverified_run_is_still_active_so_wait_keeps_blocking() -> None:
    """`wait` blocks on task liveness, not on the currency claim: the task is
    genuinely running upstream, so returning early would be a lie in the other
    direction."""

    status = build_status(
        "repo-1",
        _catalog(),
        (),
        (AuditRun("pending-run", "audit.security", "pending", "2026-07-25T20:17:17.698Z", None, None),),
        AuditRerunState("current-sha", None, None, None, {"audit.security": "old-sha"}),
    )

    assert status.items[0].current_head.state == "unverified"
    assert status.active == ("audit.security",)
