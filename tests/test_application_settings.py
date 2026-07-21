from typing import cast

from enji_guard_cli.application import Application
from enji_guard_cli.audit.ports import AuditGatewayPort
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.portfolio.models import (
    AccessInfo,
    AccessLimits,
    AccountPreferences,
    ProjectDetail,
    ProjectRef,
    RepositoryRef,
)
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort


class _Portfolio:
    def __init__(self) -> None:
        self.project = ProjectRef("p1", "Pets")
        self.repository = RepositoryRef("r1", "p1", "Pets", "acme/cat")
        self.preferences = AccountPreferences("en")

    def list_projects(self):
        return (self.project,)

    def project_detail(self, project_id: str):
        return ProjectDetail(self.project, (self.repository,))

    def get_preferences(self):
        return self.preferences

    def access(self):
        return AccessInfo("pro", True, AccessLimits(can_use_schedules=True))


def test_project_settings_keeps_language_account_wide() -> None:
    portfolio = _Portfolio()
    app = Application(
        cast(AuditGatewayPort, object()), cast(PortfolioGatewayPort, portfolio), cast(AuthSessionService, object())
    )

    settings = app.project_settings("pets")

    assert settings.project == portfolio.project
    assert settings.repositories == (portfolio.repository,)
    assert settings.account_preferences == AccountPreferences("en")


def test_access_is_typed_and_gateway_backed() -> None:
    portfolio = _Portfolio()
    app = Application(
        cast(AuditGatewayPort, object()), cast(PortfolioGatewayPort, portfolio), cast(AuthSessionService, object())
    )

    assert app.access() == AccessInfo("pro", True, AccessLimits(can_use_schedules=True))
