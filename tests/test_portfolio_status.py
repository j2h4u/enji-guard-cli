# pyright: basic

from typing import Any, cast

import pytest

from enji_guard_cli.audit.ports import AuditFreshness, AuditStatus, AuditStatusItem
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.portfolio.models import PortfolioActiveRun, ProjectDetail, ProjectRef, RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioAuditStatus
from enji_guard_cli.portfolio.status import assemble_overview, assemble_status
from enji_guard_cli.settings import FanoutSettings

FANOUT = BoundedFanout(FanoutSettings(max_concurrency=4))


class Gateway:
    def list_projects(self):
        return (ProjectRef("p1", "Pets"),)

    def project_detail(self, project_id):
        return ProjectDetail(ProjectRef("p1", "Pets"), (RepositoryRef("r1", "p1", "Pets", "acme/cat"),))


class Audits:
    def status(self, repo_id):
        return PortfolioAuditStatus(
            AuditStatus(
                repo_id,
                "new",
                (
                    AuditStatusItem(
                        "audit.security",
                        "Security",
                        AuditFreshness("new", "old", "stale"),
                        True,
                        "completed",
                        "task",
                        "completed",
                    ),
                ),
            )
        )


def test_status_preserves_sha_and_staleness_inputs() -> None:
    status = assemble_status(gateway=cast(Any, Gateway()), audits=Audits(), fanout=FANOUT)
    assert status.repositories[0].audit.summary.current_head_sha == "new"
    assert status.repositories[0].audit.summary.items[0].freshness.audited_head_sha == "old"


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
    status = assemble_status(gateway=cast(Any, SortGateway()), audits=Audits(), fanout=FANOUT, sort="weakest")

    assert [item.repository.repo_id for item in status.repositories] == ["r2", "r1"]


class OverviewGateway:
    def __init__(self) -> None:
        self.details: list[str] = []
        self.active: list[str] = []

    def list_projects(self):
        return (ProjectRef("p1", "Pets"), ProjectRef("p2", "Plants"))

    def project_detail(self, project_id):
        self.details.append(project_id)
        name = "Pets" if project_id == "p1" else "Plants"
        return ProjectDetail(
            ProjectRef(project_id, name),
            (
                RepositoryRef(f"r-{project_id}", project_id, name, f"acme/{name.lower()}", scores={"tests": 90}),
                RepositoryRef(f"r2-{project_id}", project_id, name, f"acme/a-{name.lower()}", scores={"tests": 40}),
            ),
        )

    def project_active_runs(self, project_id):
        self.active.append(project_id)
        return (
            PortfolioActiveRun(
                f"r-{project_id}", task_id=f"task-{project_id}", status="running", completed_at="2026-07-16"
            ),
            PortfolioActiveRun(f"r2-{project_id}", task_id=f"task2-{project_id}", status="running"),
        )


def test_overview_uses_project_aggregates_and_filters_before_detail_reads() -> None:
    gateway = OverviewGateway()

    overview = assemble_overview(gateway=cast(Any, gateway), fanout=FANOUT, project="Pets")

    assert [item.project.project_id for item in overview.projects] == ["p1"]
    assert overview.projects[0].repositories[0].active_runs[0].task_id == "task-p1"
    assert gateway.details == ["p1"]
    assert gateway.active == ["p1"]


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("name", ["r2-p1", "r-p1"]),
        ("weakest", ["r2-p1", "r-p1"]),
        ("overall", ["r2-p1", "r-p1"]),
        ("latest-audit", ["r-p1", "r2-p1"]),
    ],
)
def test_overview_sorts_aggregate_repository_data(sort: str, expected: list[str]) -> None:
    overview = assemble_overview(
        gateway=cast(Any, OverviewGateway()), fanout=FANOUT, project="Pets", sort=cast(Any, sort)
    )

    assert [item.repository.repo_id for item in overview.projects[0].repositories] == expected
