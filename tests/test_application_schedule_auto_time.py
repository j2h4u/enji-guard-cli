import pytest

from application_builder import ApplicationStubs
from enji_guard_cli.application import Application
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import AuditSchedule
from enji_guard_cli.portfolio.models import RepositoryIdentity, RepositoryProvider, RepositoryRef


class _AuditGateway:
    def __init__(self, schedule: AuditSchedule) -> None:
        self.schedule = schedule
        self.set_calls = 0

    def list_schedules(self, _repo_id: str) -> tuple[AuditSchedule, ...]:
        return (self.schedule,)

    def set_schedule(self, _repo_id: str, _audit_key: str, schedule: AuditSchedule) -> AuditSchedule:
        self.set_calls += 1
        self.schedule = schedule
        return schedule


def test_schedule_auto_time_skips_write_when_already_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = AuditSchedule("audit.security", True, "workdays", None, 1, "00:00", "auto", "UTC")
    gateway = _AuditGateway(current)
    application = ApplicationStubs(audit_gateway=gateway).build()
    monkeypatch.setattr(
        Application,
        "_write_targets",
        lambda _self, _repo, _project, _scope: (
            RepositoryRef(
                "repo-1",
                "project-1",
                "Pets",
                RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
                web_url="https://example.test/repository",
                provider_repo_id="provider-test",
            ),
        ),
    )
    monkeypatch.setattr(
        Application,
        "audit_catalog",
        lambda _self: AuditCatalog(
            published_audits=(AuditDefinition("audit.security", "Security", "vulns", "audit"),),
            recon=AuditDefinition("audit.recon", "Recon", None, "recon"),
        ),
    )

    result = application.schedule_auto_time("repo-1")

    assert result == (current,)
    assert gateway.set_calls == 0
