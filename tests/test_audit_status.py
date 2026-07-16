from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import AuditRerunState, AuditRun, AuditTaskLink
from enji_guard_cli.audit.status import build_status


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

    assert status.items[0].task_lifecycle == "queued"
    assert status.items[0].can_read is False
