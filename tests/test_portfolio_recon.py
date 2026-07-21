# pyright: basic

from enji_guard_cli.audit.ports import AuditStatus
from enji_guard_cli.portfolio.models import RepositoryIdentity, RepositoryProvider, RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioAuditStatus
from enji_guard_cli.portfolio.recon import RECON_ACTION_KEY, start_recon


class Audits:
    def status(self, repo_id):
        return PortfolioAuditStatus(AuditStatus(repo_id, "sha", ()))


class Starter:
    def __init__(self):
        self.action = None

    def start(self, repo_id, project_id, action_key):
        self.action = (repo_id, project_id, action_key)
        from enji_guard_cli.audit.ports import AuditRunResult

        return AuditRunResult("task", "queued")


def test_recon_uses_canonical_audit_identity() -> None:
    starter = Starter()
    result = start_recon(
        RepositoryRef(
            "r1",
            "p1",
            "Pets",
            RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
            web_url="https://example.test/repository",
            provider_repo_id="provider-test",
        ),
        audits=Audits(),
        starter=starter,
    )
    assert result.state == "started"
    assert starter.action == ("r1", "p1", RECON_ACTION_KEY)
