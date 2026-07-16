from typing import cast

import pytest

from enji_guard_cli.portfolio.models import ProjectDetail, ProjectRef, RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort
from enji_guard_cli.portfolio.selectors import GatewayPortfolioTargetService


class Gateway:
    def list_projects(self) -> tuple[ProjectRef, ...]:
        return (ProjectRef("p1", "Pets"), ProjectRef("p2", "Work"))

    def project_detail(self, project_id: str) -> ProjectDetail:
        if project_id == "p1":
            return ProjectDetail(
                ProjectRef("p1", "Pets"),
                (RepositoryRef("r1", "p1", "Pets", "Acme/Cat"),),
                ("https://pets.example",),
                {"https://pets.example": ("r1",)},
            )
        return ProjectDetail(ProjectRef("p2", "Work"), (RepositoryRef("r2", "p2", "Work", "acme/dog"),))


def test_target_service_resolves_and_expands_explicit_scopes() -> None:
    service = GatewayPortfolioTargetService(cast(PortfolioGatewayPort, Gateway()))

    assert service.resolve_project("PETS").project_id == "p1"
    assert service.resolve_repository("acme/cat", project="pets").repo_id == "r1"
    assert [repo.repo_id for repo in service.write_targets(None, "p1", all_repos=True)] == ["r1"]
    assert [repo.repo_id for repo in service.write_targets(None, None, all_projects=True)] == ["r1", "r2"]


def test_target_service_rejects_conflicting_batch_scope() -> None:
    service = GatewayPortfolioTargetService(cast(PortfolioGatewayPort, Gateway()))

    with pytest.raises(ValueError, match="all-repos or --all-projects"):
        service.write_targets(None, "p1", all_repos=True, all_projects=True)


def test_target_service_preserves_linked_website_mapping() -> None:
    service = GatewayPortfolioTargetService(cast(PortfolioGatewayPort, Gateway()))

    assert service.linked_website_mapping("p1") == {"https://pets.example": ("r1",)}
