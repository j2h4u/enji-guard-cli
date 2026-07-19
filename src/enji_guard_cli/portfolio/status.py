"""Portfolio status assembly over a typed Audit status projection."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

from enji_guard_cli.portfolio.models import PortfolioActiveRun, ProjectDetail, ProjectRef, RepositoryRef
from enji_guard_cli.portfolio.ports import (
    AuditStatusReader,
    PortfolioAuditStatus,
    PortfolioGatewayPort,
)
from enji_guard_cli.settings import RepositorySortName


@dataclass(frozen=True, slots=True)
class RepositoryStatus:
    repository: RepositoryRef
    audit: PortfolioAuditStatus

    @property
    def active(self) -> bool:
        return bool(self.audit.active_runs)


@dataclass(frozen=True, slots=True)
class ProjectStatus:
    project: ProjectRef
    repositories: tuple[RepositoryStatus, ...]


@dataclass(frozen=True, slots=True)
class PortfolioStatus:
    observed_at: str
    projects: tuple[ProjectStatus, ...]

    @property
    def repositories(self) -> tuple[RepositoryStatus, ...]:
        return tuple(repo for project in self.projects for repo in project.repositories)


@dataclass(frozen=True, slots=True)
class RepositoryOverview:
    repository: RepositoryRef
    active_runs: tuple[PortfolioActiveRun, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectOverview:
    project: ProjectRef
    repositories: tuple[RepositoryOverview, ...]


@dataclass(frozen=True, slots=True)
class PortfolioOverview:
    observed_at: str
    projects: tuple[ProjectOverview, ...]


def repository_status(repository: RepositoryRef, *, audits: AuditStatusReader) -> RepositoryStatus:
    status = audits.status(repository.repo_id)
    return RepositoryStatus(repository=repository, audit=status)


def project_status(
    detail: ProjectDetail, *, audits: AuditStatusReader, sort: RepositorySortName = "default"
) -> ProjectStatus:
    repositories = tuple(repository_status(repo, audits=audits) for repo in detail.repositories)
    return ProjectStatus(detail.project, _sort_repositories(repositories, sort))


def assemble_status(
    *, gateway: PortfolioGatewayPort, audits: AuditStatusReader, sort: RepositorySortName = "default"
) -> PortfolioStatus:
    projects = tuple(
        project_status(gateway.project_detail(project.project_id), audits=audits, sort=sort)
        for project in gateway.list_projects()
    )
    return PortfolioStatus(datetime.now(UTC).isoformat(), projects)


def assemble_overview(
    *,
    gateway: PortfolioGatewayPort,
    project: str | None = None,
    sort: RepositorySortName = "default",
) -> PortfolioOverview:
    """Build the fast aggregate view without per-repository audit requests."""

    projects = gateway.list_projects()
    if project is not None:
        projects = tuple(item for item in projects if project in {item.project_id, item.name})
    if not projects:
        return PortfolioOverview(datetime.now(UTC).isoformat(), ())
    with ThreadPoolExecutor(max_workers=len(projects)) as executor:
        overviews = tuple(executor.map(lambda item: _project_overview(gateway, item, sort), projects))
    return PortfolioOverview(datetime.now(UTC).isoformat(), overviews)


def _project_overview(gateway: PortfolioGatewayPort, project: ProjectRef, sort: RepositorySortName) -> ProjectOverview:
    with ThreadPoolExecutor(max_workers=2) as executor:
        detail_future = executor.submit(gateway.project_detail, project.project_id)
        runs_future = executor.submit(gateway.project_active_runs, project.project_id)
        detail = detail_future.result()
        active_runs = runs_future.result()
    runs_by_repo: dict[str, list[PortfolioActiveRun]] = {}
    for run in active_runs:
        runs_by_repo.setdefault(run.repo_id, []).append(run)
    repositories = tuple(
        RepositoryOverview(repository, tuple(runs_by_repo.get(repository.repo_id, ())))
        for repository in detail.repositories
    )
    return ProjectOverview(detail.project, _sort_overview_repositories(repositories, sort))


def _sort_overview_repositories(
    repositories: tuple[RepositoryOverview, ...], sort: RepositorySortName
) -> tuple[RepositoryOverview, ...]:
    if sort == "default":
        return repositories
    sorters = {
        "name": lambda: sorted(repositories, key=_overview_name),
        "weakest": lambda: sorted(repositories, key=lambda item: _overview_score(item, weakest=True)),
        "overall": lambda: sorted(repositories, key=lambda item: _overview_score(item, weakest=False)),
        "latest-audit": lambda: sorted(repositories, key=_overview_latest_audit_at, reverse=True),
    }
    try:
        return tuple(sorters[sort]())
    except KeyError as exc:
        raise ValueError(f"unknown repository sort: {sort}") from exc


def _overview_name(item: RepositoryOverview) -> str:
    return (item.repository.full_name or "").casefold()


def _overview_score(item: RepositoryOverview, *, weakest: bool) -> tuple[float, str]:
    values = [float(value) for value in item.repository.scores.values() if value is not None]
    score = (min(values) if weakest else sum(values) / len(values)) if values else float("inf")
    return score, item.repository.full_name or ""


def _overview_latest_audit_at(item: RepositoryOverview) -> str:
    return max((run.completed_at or "" for run in item.active_runs), default="")


def _sort_repositories(
    repositories: tuple[RepositoryStatus, ...], sort: RepositorySortName
) -> tuple[RepositoryStatus, ...]:
    if sort == "default":
        return repositories
    if sort == "name":
        return tuple(sorted(repositories, key=lambda item: (item.repository.full_name or "").casefold()))
    if sort in {"weakest", "overall"}:

        def score(item: RepositoryStatus) -> float:
            values = [float(value) for value in item.repository.scores.values() if value is not None]
            if not values:
                return float("inf")
            return min(values) if sort == "weakest" else sum(values) / len(values)

        return tuple(sorted(repositories, key=lambda item: (score(item), item.repository.full_name or "")))
    if sort == "latest-audit":
        return tuple(sorted(repositories, key=_latest_audit_at, reverse=True))
    raise ValueError(f"unknown repository sort: {sort}")


def _latest_audit_at(item: RepositoryStatus) -> str:
    return max((audit.completed_at or "" for audit in item.audit.summary.items), default="")


def status_for_repo(
    repo: str, project: str | None = None, *, gateway: PortfolioGatewayPort, audits: AuditStatusReader
) -> tuple[RepositoryStatus, ...]:
    from enji_guard_cli.portfolio.selectors import resolve_repository

    targets = tuple(
        repo_ref
        for project_ref in gateway.list_projects()
        for repo_ref in gateway.project_detail(project_ref.project_id).repositories
    )
    return (repository_status(resolve_repository(targets, repo, project=project), audits=audits),)
