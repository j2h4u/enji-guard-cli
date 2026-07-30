from application_builder import ApplicationStubs, RecordingTargetService, WriteTargetsCall, repository
from enji_guard_cli.application.mutations import MutationReason
from enji_guard_cli.audit.ports import AuditCatalogAction, AuditCatalogResult, AuditSchedule

_REPOSITORY = repository("acme/cat", repo_id="repo-1")


class _AuditGateway:
    def __init__(self, schedule: AuditSchedule) -> None:
        self.schedule = schedule
        self.set_calls = 0

    def catalog(self) -> AuditCatalogResult:
        return AuditCatalogResult(
            actions=(
                AuditCatalogAction("audit.recon", "Recon", "workflow", "draft", None, "recon"),
                AuditCatalogAction("audit.security", "Security", "audit", "published", "vulns", "audit"),
            )
        )

    def list_schedules(self, _repo_id: str) -> tuple[AuditSchedule, ...]:
        return (self.schedule,)

    def set_schedule(self, _repo_id: str, _audit_key: str, schedule: AuditSchedule) -> AuditSchedule:
        self.set_calls += 1
        self.schedule = schedule
        return schedule


def test_schedule_auto_time_skips_write_when_already_auto() -> None:
    current = AuditSchedule("audit.security", True, "workdays", None, 1, "00:00", "auto", "UTC")
    gateway = _AuditGateway(current)
    targets = RecordingTargetService((_REPOSITORY,))
    application = ApplicationStubs(audit_gateway=gateway, target_service=targets).build()

    result = application.subscriptions.schedule_auto_time("repo-1")

    assert result.status == "completed"
    assert (result.total, result.changed, result.unchanged, result.failed) == (1, 0, 1, 0)
    assert result.results[0].target.selector == "security"
    assert result.results[0].reason is MutationReason.ALREADY_EFFECTIVE
    assert gateway.set_calls == 0
    assert targets.write_targets_calls == [WriteTargetsCall("repo-1", None, False, False, "mutation")]
