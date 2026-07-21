"""Repository membership, connection and move workflows."""

from __future__ import annotations

from enji_guard_cli.portfolio.errors import PortfolioNotFoundError
from enji_guard_cli.portfolio.models import OperationResult, RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort
from enji_guard_cli.portfolio.selectors import (
    GatewayPortfolioTargetService,
    parse_github_repo,
    resolve_project,
    resolve_repository,
)


def all_targets(*, gateway: PortfolioGatewayPort) -> tuple[RepositoryRef, ...]:
    return GatewayPortfolioTargetService(gateway).targets()


def add_repository(github_repo: str, project: str | None, *, gateway: PortfolioGatewayPort) -> OperationResult:
    selected = resolve_project(gateway.list_projects(), project)
    owner, name = parse_github_repo(github_repo)
    canonical_name = f"{owner}/{name}"
    existing = next(
        (
            repo
            for repo in gateway.project_detail(selected.project_id).repositories
            if repo.full_name is not None and repo.full_name.casefold() == canonical_name.casefold()
        ),
        None,
    )
    if existing is not None:
        if existing.connected is not True:
            existing = gateway.connect_repository(selected.project_id, existing.repo_id)
        return OperationResult("already_present", repository=existing, project=selected)
    return OperationResult(
        "added", repository=gateway.add_repository(selected.project_id, owner, name), project=selected
    )


def remove_repository(repo: str, project: str | None, *, gateway: PortfolioGatewayPort) -> OperationResult:
    try:
        target = resolve_repository(all_targets(gateway=gateway), repo, project=project)
    except PortfolioNotFoundError:
        return OperationResult("already_absent", message=f"repository already absent: {repo}")
    gateway.remove_repository(target.project_id, target.repo_id)
    return OperationResult("removed", repository=target)


def move_repository(
    repo: str, source_project: str | None, target_project: str, *, gateway: PortfolioGatewayPort
) -> OperationResult:
    target = resolve_repository(all_targets(gateway=gateway), repo, project=source_project)
    destination = resolve_project(gateway.list_projects(), target_project)
    if target.project_id == destination.project_id:
        return OperationResult(
            "already_in_target",
            repository=target,
            source_project_id=target.project_id,
            target_project_id=destination.project_id,
        )
    preflight = gateway.preflight_repository_move(target.project_id, target.repo_id, destination.project_id)
    if not preflight.allowed:
        raise ValueError(preflight.message or "repository move preflight failed")
    moved = gateway.move_repository(target.project_id, target.repo_id, destination.project_id)
    return OperationResult(
        "moved", repository=moved, source_project_id=target.project_id, target_project_id=destination.project_id
    )
