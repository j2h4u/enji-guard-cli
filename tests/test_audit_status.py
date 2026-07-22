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
