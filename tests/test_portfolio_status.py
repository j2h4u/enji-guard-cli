# pyright: basic

from typing import Any, cast

from enji_guard_cli.portfolio.models import ProjectDetail, ProjectRef, RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioAuditStatus
from enji_guard_cli.portfolio.status import assemble_status


class Gateway:
    def list_projects(self):
        return (ProjectRef("p1", "Pets"),)

    def project_detail(self, project_id):
        return ProjectDetail(ProjectRef("p1", "Pets"), (RepositoryRef("r1", "p1", "Pets", "acme/cat"),))


class Audits:
    def status(self, repo_id):
        return PortfolioAuditStatus("new", {"audit.security": "old"})


def test_status_preserves_sha_and_staleness_inputs() -> None:
    status = assemble_status(gateway=cast(Any, Gateway()), audits=Audits())
    assert status.repositories[0].current_head_sha == "new"
    assert status.repositories[0].audited_head_shas["audit.security"] == "old"


class SortGateway:
    def list_projects(self):
        return (ProjectRef("p1", "Pets"),)

    def project_detail(self, project_id):
        return ProjectDetail(
            ProjectRef("p1", "Pets"),
            (
                RepositoryRef("r1", "p1", "Pets", "acme/zebra", scores={"tests": 90}),
                RepositoryRef("r2", "p1", "Pets", "acme/ant", scores={"tests": 40}),
            ),
        )


def test_status_sorts_repository_inventory_by_weakest_score() -> None:
    status = assemble_status(gateway=cast(Any, SortGateway()), audits=Audits(), sort="weakest")

    assert [item.repository.repo_id for item in status.repositories] == ["r2", "r1"]
