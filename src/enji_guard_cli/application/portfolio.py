"""Portfolio use-cases: projects, repositories, recon, status, and account."""

from dataclasses import dataclass

from enji_guard_cli.application.audit import AuditReconFactory
from enji_guard_cli.application.catalog import AuditCatalogService
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.portfolio.models import (
    AccessInfo,
    AccountPreferences,
    OperationResult,
    ProjectRef,
    ProjectSettings,
    RepositoryRef,
)
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort, PortfolioTargetService
from enji_guard_cli.portfolio.projects import create_project as create_project_use_case
from enji_guard_cli.portfolio.projects import delete_project as delete_project_use_case
from enji_guard_cli.portfolio.projects import rename_project as rename_project_use_case
from enji_guard_cli.portfolio.recon import recon_after_add
from enji_guard_cli.portfolio.recon import start_recon as start_recon_use_case
from enji_guard_cli.portfolio.repositories import add_repository, move_repository, remove_repository
from enji_guard_cli.portfolio.status import PortfolioOverview, RepositoryStatus, assemble_overview, status_for_repo
from enji_guard_cli.settings import RepositorySortName


@dataclass(frozen=True, slots=True)
class PortfolioFacade:
    """Own project/repository membership plus the status views built on it."""

    gateway: PortfolioGatewayPort
    targets: PortfolioTargetService
    catalog: AuditCatalogService
    audits: AuditReconFactory
    fanout: BoundedFanout

    def list_projects(self) -> tuple[ProjectRef, ...]:
        return self.gateway.list_projects()

    def create_project(self, name: str) -> OperationResult:
        return create_project_use_case(name, gateway=self.gateway)

    def rename_project(self, project: str, name: str) -> OperationResult:
        return rename_project_use_case(project, name, gateway=self.gateway)

    def delete_project(self, project: str) -> OperationResult:
        return delete_project_use_case(project, gateway=self.gateway)

    def add_repository(
        self, repo: str, project: str | None = None, repo_access_credential_id: str | None = None
    ) -> OperationResult:
        result = add_repository(
            repo, project, gateway=self.gateway, repo_access_credential_id=repo_access_credential_id
        )
        if result.repository is None:
            return result
        recon_ports = self.audits.recon(self.catalog.audits())
        recon = recon_after_add(result.repository, audits=recon_ports, starter=recon_ports)
        return OperationResult(
            result.state,
            project=result.project,
            repository=result.repository,
            message=result.message,
            recon=recon,
        )

    def remove_repository(self, repo: str, project: str | None = None) -> OperationResult:
        return remove_repository(repo, project, gateway=self.gateway)

    def move_repository(self, repo: str, source_project: str | None, target_project: str) -> OperationResult:
        return move_repository(repo, source_project, target_project, gateway=self.gateway)

    def resolve_repository(self, repo: str, project: str | None = None) -> RepositoryRef:
        return self.targets.resolve_repository(repo, project=project)

    def recon_start(self, repo: str, project: str | None = None) -> object:
        target = self.targets.resolve_repository(repo, project=project)
        recon_ports = self.audits.recon(self.catalog.audits())
        return start_recon_use_case(target, audits=recon_ports, starter=recon_ports)

    def portfolio_overview(self, project: str | None = None, sort: RepositorySortName = "default") -> PortfolioOverview:
        return assemble_overview(gateway=self.gateway, fanout=self.fanout, project=project, sort=sort)

    def repository_status(self, repo: str, project: str | None = None) -> tuple[RepositoryStatus, ...]:
        audits = self.audits.recon(self.catalog.audits())
        return status_for_repo(repo, project, gateway=self.gateway, audits=audits, fanout=self.fanout)

    def language(self) -> AccountPreferences:
        return self.gateway.get_preferences()

    def set_language(self, language: str) -> AccountPreferences:
        return self.gateway.set_preferences(AccountPreferences(language))

    def project_settings(self, project: str | None = None) -> ProjectSettings:
        """Return project membership plus account preferences exactly once."""
        selected = self.targets.resolve_project(project)
        detail = self.gateway.project_detail(selected.project_id)
        return ProjectSettings(
            project=detail.project,
            repositories=detail.repositories,
            account_preferences=self.gateway.get_preferences(),
        )

    def access(self) -> AccessInfo:
        """Return account plan/limits through the typed Portfolio gateway."""
        return self.gateway.access()


__all__ = ["PortfolioFacade"]
