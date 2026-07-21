# pyright: basic

from enji_guard_cli.audit.ports import AuditStatus
from enji_guard_cli.portfolio.models import RepositoryIdentity, RepositoryProvider, RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioAuditStatus
from enji_guard_cli.portfolio.recon import start_recon


class Audits:
    def status(self, repo_id):
        return PortfolioAuditStatus(AuditStatus(repo_id, "sha", ()), active_runs=())


class Starter:
    def start(self, repo_id, project_id, action_key):
        from enji_guard_cli.audit.ports import AuditRunResult

        return AuditRunResult("task", "queued")


def test_recon_repeat_safe_when_done() -> None:
    target = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
        recon_done=True,
        web_url="https://example.test/repository",
        provider_repo_id="provider-test",
    )
    assert start_recon(target, audits=Audits(), starter=Starter()).state == "unchanged"
