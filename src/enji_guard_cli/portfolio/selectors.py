"""Project and repository selector resolution."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from enji_guard_cli.portfolio.errors import PortfolioNotFoundError
from enji_guard_cli.portfolio.models import ProjectRef, RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort, SelectorResolver
from enji_guard_cli.portfolio.scopes import MutationScope


def project_matches(project: ProjectRef, selector: str) -> bool:
    return project.project_id.casefold() == selector.casefold() or (
        project.name is not None and project.name.casefold() == selector.casefold()
    )


def resolve_project(projects: Sequence[ProjectRef], selector: str | None = None) -> ProjectRef:
    if selector is None:
        if len(projects) == 1:
            return projects[0]
        raise ValueError(f"project selector is ambiguous; pass --project. candidates: {_project_candidates(projects)}")
    matches = [project for project in projects if project_matches(project, selector)]
    if not matches:
        raise PortfolioNotFoundError(
            f"project selector matched no projects: {selector}. candidates: {_project_candidates(projects)}"
        )
    if len(matches) > 1:
        raise ValueError(f"project selector is ambiguous; pass --project. candidates: {_project_candidates(matches)}")
    return matches[0]


def resolve_project_id(projects: Sequence[ProjectRef], selector: str | None = None) -> str:
    return resolve_project(projects, selector).project_id


def project_candidates(projects: Sequence[ProjectRef]) -> str:
    return _project_candidates(projects)


def repository_matches(repository: RepositoryRef, selector: str) -> bool:
    normalized = selector.casefold()
    return repository.repo_id.casefold() == normalized or (
        repository.full_name is not None and repository.full_name.casefold() == normalized
    )


def resolve_repository(targets: Sequence[RepositoryRef], selector: str, *, project: str | None = None) -> RepositoryRef:
    scoped = [
        target
        for target in targets
        if project is None
        or project.casefold() in {target.project_id.casefold(), (target.project_name or "").casefold()}
    ]
    matches = [target for target in scoped if repository_matches(target, selector)]
    if not matches:
        raise PortfolioNotFoundError(f"repo selector matched no repos: {selector}")
    if len(matches) > 1:
        candidates = ", ".join(repository_candidate(target) for target in matches)
        raise ValueError(f"repo selector is ambiguous: {selector}. candidates: {candidates}")
    return matches[0]


def repository_candidate(target: RepositoryRef) -> str:
    full_name = target.full_name or target.repo_id
    project = target.project_name or target.project_id
    return f"{full_name} in {project} ({target.repo_id})"


def repository_targets(details: Sequence[ProjectDetailLike]) -> tuple[RepositoryRef, ...]:
    return tuple(repository for detail in details for repository in detail.repositories)


class ProjectDetailLike(Protocol):
    """Structural helper for callers that only need ``repositories``."""

    repositories: tuple[RepositoryRef, ...]


class GatewaySelectorResolver(SelectorResolver):
    """Selector resolver backed by the typed Portfolio gateway."""

    def __init__(self, gateway: PortfolioGatewayPort) -> None:
        self.gateway = gateway

    def resolve_project(self, selector: str | None = None) -> ProjectRef:
        return resolve_project(self.gateway.list_projects(), selector)

    def resolve_repository(self, selector: str, *, project: str | None = None) -> RepositoryRef:
        targets = tuple(
            repo
            for project_ref in self.gateway.list_projects()
            for repo in self.gateway.project_detail(project_ref.project_id).repositories
        )
        return resolve_repository(targets, selector, project=project)


class GatewayPortfolioTargetService(GatewaySelectorResolver):
    """Gateway-backed Portfolio target selection and explicit scope expansion."""

    def targets(self, repo: str | None = None, project: str | None = None) -> tuple[RepositoryRef, ...]:
        projects = self.gateway.list_projects()
        selected = projects if project is None else (resolve_project(projects, project),)
        repositories = tuple(
            repository
            for project_ref in selected
            for repository in self.gateway.project_detail(project_ref.project_id).repositories
        )
        if repo is None:
            return repositories
        return (resolve_repository(repositories, repo, project=project),)

    def write_targets(
        self,
        repo: str | None,
        project: str | None,
        *,
        all_repos: bool = False,
        all_projects: bool = False,
        operation: str = "mutation",
    ) -> tuple[RepositoryRef, ...]:
        scope = MutationScope.from_args(
            repo,
            project,
            all_repos=all_repos,
            all_projects=all_projects,
            operation=operation,
        )
        if scope.kind == "all_projects":
            return self.targets()
        if scope.kind == "all_repos":
            return self.targets(project=scope.project)
        return self.targets(scope.repo, scope.project)

    def linked_website_mapping(self, project_id: str) -> dict[str, tuple[str, ...]]:
        """Return website-to-repository links as an immutable-value mapping."""

        detail = self.gateway.project_detail(project_id)
        return {
            url: tuple(repo_ids)
            for url, repo_ids in detail.linked_website_repo_ids.items()
            if url in detail.linked_websites
        }


def parse_github_repo(value: str) -> tuple[str, str]:
    owner, separator, name = value.strip().partition("/")
    owner = owner.strip()
    name = name.strip()
    if not separator or not owner or not name or "/" in name:
        raise ValueError("repo must be an owner/name GitHub slug")
    return owner, name


def validated_project_name(name: str) -> str:
    value = name.strip()
    if not value:
        raise ValueError("project name must not be empty")
    return value


def _project_candidates(projects: Sequence[ProjectRef]) -> str:
    return ", ".join(f"{p.name} ({p.project_id})" if p.name else p.project_id for p in projects)


resolve_single_project = resolve_project
resolve_single_project_id = resolve_project_id
resolve_single_repository = resolve_repository
resolve_single_repo_target = resolve_repository
