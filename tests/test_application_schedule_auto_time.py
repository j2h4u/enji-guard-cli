from application_builder import ApplicationStubs
from enji_guard_cli.audit.ports import AuditCatalogAction, AuditCatalogResult, AuditSchedule
from enji_guard_cli.portfolio.models import RepositoryIdentity, RepositoryProvider, RepositoryRef

_REPOSITORY = RepositoryRef(
    "repo-1",
    "project-1",
    "Pets",
    RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
    web_url="https://example.test/repository",
    provider_repo_id="provider-test",
)


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


class _TargetService:
    def write_targets(self, *_args: object, **_kwargs: object) -> tuple[RepositoryRef, ...]:
        return (_REPOSITORY,)


def test_schedule_auto_time_skips_write_when_already_auto() -> None:
    current = AuditSchedule("audit.security", True, "workdays", None, 1, "00:00", "auto", "UTC")
    gateway = _AuditGateway(current)
    application = ApplicationStubs(audit_gateway=gateway, target_service=_TargetService()).build()

    result = application.subscriptions.schedule_auto_time("repo-1")

    assert result == (current,)
    assert gateway.set_calls == 0
