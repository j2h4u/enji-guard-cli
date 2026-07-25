from typing import cast

from application_builder import ApplicationStubs
from enji_guard_cli.application import Application
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.portfolio.models import (
    AccessInfo,
    AccessLimits,
    AccountPreferences,
    ProjectDetail,
    ProjectRef,
    RepositoryIdentity,
    RepositoryProvider,
    RepositoryRef,
)
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort
from enji_guard_cli.portfolio.selectors import GatewayPortfolioTargetService
from enji_guard_cli.settings import default_settings


class _Portfolio:
    def __init__(self) -> None:
        self.project = ProjectRef("p1", "Pets")
        self.repository = RepositoryRef(
            "r1",
            "p1",
            "Pets",
            RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
            web_url="https://example.test/repository",
            provider_repo_id="provider-test",
        )
        self.preferences = AccountPreferences("en")

    def list_projects(self):
        return (self.project,)

    def project_detail(self, project_id: str):
        return ProjectDetail(self.project, (self.repository,))

    def get_preferences(self):
        return self.preferences

    def access(self):
        return AccessInfo("pro", True, AccessLimits(can_use_schedules=True))


def _facades(portfolio: _Portfolio) -> Application:
    gateway = cast(PortfolioGatewayPort, portfolio)
    targets = GatewayPortfolioTargetService(gateway, BoundedFanout(default_settings().fanout))
    return ApplicationStubs(portfolio_gateway=portfolio, target_service=targets).build()


def test_project_settings_keeps_language_account_wide() -> None:
    portfolio = _Portfolio()
    app = _facades(portfolio)

    settings = app.portfolio.project_settings("pets")

    assert settings.project == portfolio.project
    assert settings.repositories == (portfolio.repository,)
    assert settings.account_preferences == AccountPreferences("en")


def test_access_is_typed_and_gateway_backed() -> None:
    portfolio = _Portfolio()
    app = _facades(portfolio)

    assert app.portfolio.access() == AccessInfo("pro", True, AccessLimits(can_use_schedules=True))
