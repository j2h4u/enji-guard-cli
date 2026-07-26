"""Project projection from Portfolio membership into Audit's neutral model."""

from dataclasses import dataclass

from enji_guard_cli.audit.ports import AuditProject, AuditRepository, AuditWebsite
from enji_guard_cli.portfolio.models import RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort


@dataclass(frozen=True, slots=True)
class AuditProjectSource:
    """Translate one Portfolio project detail into the Audit project model.

    Audit start and read workflows need project membership but must not learn
    Portfolio's vocabulary, so the translation lives at the application seam.
    """

    gateway: PortfolioGatewayPort

    def __call__(self, project_id: str) -> AuditProject:
        detail = self.gateway.project_detail(project_id)
        return AuditProject(
            project_id=detail.project.project_id,
            repositories=tuple(_audit_repository(repo) for repo in detail.repositories),
            linked_websites=tuple(
                AuditWebsite(url, tuple(detail.linked_website_repo_ids.get(url, ()))) for url in detail.linked_websites
            ),
        )


def _audit_repository(repo: RepositoryRef) -> AuditRepository:
    return AuditRepository(
        repo.repo_id,
        repo.identity.provider.value,
        repo.identity.locator,
        repo.connected is True,
        repo.web_url,
    )


__all__ = ["AuditProjectSource"]
