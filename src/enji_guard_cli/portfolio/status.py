"""Portfolio status assembly over a typed Audit status projection."""

from dataclasses import dataclass
from datetime import UTC, datetime

from enji_guard_cli.audit.ports import AuditItemStatus, AuditRun
from enji_guard_cli.portfolio.models import ProjectDetail, ProjectRef, RepositoryRef
from enji_guard_cli.portfolio.ports import AuditStatusReader, PortfolioGatewayPort, PortfolioStatusPort
from enji_guard_cli.settings import RepositorySortName


@dataclass(frozen=True, slots=True)
class RepositoryStatus:
    repository: RepositoryRef
    current_head_sha: str | None
    audited_head_shas: dict[str, str | None]
    audits: tuple[AuditItemStatus, ...]
    active_runs: tuple[AuditRun, ...]
    last_audit_at: str | None

    @property
    def stale_actions(self) -> tuple[str, ...]:
        return tuple(
            item.action_key
            for item in self.audits
            if item.current_head_sha is not None
            and item.audited_head_sha is not None
            and item.current_head_sha != item.audited_head_sha
        )

    @property
    def fresh(self) -> bool:
        return not self.stale_actions

    @property
    def freshness(self) -> str:
        states = {
            "stale"
            if item.current_head_sha is not None
            and item.audited_head_sha is not None
            and item.current_head_sha != item.audited_head_sha
            else "fresh"
            if item.current_head_sha is not None and item.audited_head_sha is not None
            else "unknown"
            for item in self.audits
        }
        if len(states) > 1:
            return "mixed"
        return next(iter(states), "unknown")

    @property
    def active(self) -> bool:
        return bool(self.active_runs)

    @property
    def complete(self) -> bool:
        return not self.active and bool(self.audits) and all(item.can_read for item in self.audits)


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


def repository_status(repository: RepositoryRef, *, audits: AuditStatusReader) -> RepositoryStatus:
    status = audits.status(repository.repo_id)
    return RepositoryStatus(
        repository=repository,
        current_head_sha=status.current_head_sha,
        audited_head_shas=dict(status.audited_head_shas),
        audits=status.audits,
        active_runs=status.active_runs,
        last_audit_at=status.last_audit_at,
    )


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
        return tuple(sorted(repositories, key=lambda item: item.last_audit_at or "", reverse=True))
    raise ValueError(f"unknown repository sort: {sort}")


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


repo_status = status_for_repo
portfolio_status = assemble_status


def portfolio_status_port(port: PortfolioStatusPort) -> PortfolioStatus:
    """Assemble status from a ``PortfolioStatusPort`` without exposing gateway details."""

    return assemble_status(gateway=port.gateway, audits=port.audits)
