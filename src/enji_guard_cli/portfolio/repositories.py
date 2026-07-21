"""Repository membership, connection and move workflows."""

from __future__ import annotations

from enji_guard_cli.portfolio.errors import PortfolioNotFoundError
from enji_guard_cli.portfolio.models import OperationResult, RepositoryIdentitySource, RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort
from enji_guard_cli.portfolio.selectors import (
    GatewayPortfolioTargetService,
    parse_repository_selector,
    resolve_project,
    resolve_repository,
)


def same_upstream_repository(left: RepositoryRef, right: RepositoryRef) -> bool:
    """Match refreshed records without conflating provider and Enji IDs."""

    if (
        left.identity_source is RepositoryIdentitySource.PROVIDER
        and right.identity_source is RepositoryIdentitySource.PROVIDER
    ):
        return left.stable_identity_key == right.stable_identity_key
    # A provider ID may appear only after connection/verification.  The Enji
    # membership ID proves continuity across that transition and across
    # provider-side renames while no native ID is available, but only within
    # the same provider namespace and host.
    return (
        left.repo_id == right.repo_id
        and left.identity.provider is right.identity.provider
        and left.identity.host == right.identity.host
    )


def reconcile_repository(existing: RepositoryRef, refreshed: RepositoryRef) -> RepositoryRef:
    """Accept provider refreshes, including locator changes caused by renames."""

    if not same_upstream_repository(existing, refreshed):
        raise ValueError("repository refresh changed provider identity")
    return refreshed


def all_targets(*, gateway: PortfolioGatewayPort) -> tuple[RepositoryRef, ...]:
    return GatewayPortfolioTargetService(gateway).targets()


def add_repository(
    repo: str,
    project: str | None,
    *,
    gateway: PortfolioGatewayPort,
    repo_access_credential_id: str | None = None,
) -> OperationResult:
    identity = parse_repository_selector(repo)
    selected = resolve_project(gateway.list_projects(), project)
    existing = next(
        (repo for repo in gateway.project_detail(selected.project_id).repositories if repo.identity.matches(identity)),
        None,
    )
    if existing is not None:
        if existing.connected is not True:
            existing = gateway.connect_repository(selected.project_id, existing.repo_id)
        return OperationResult("already_present", repository=existing, project=selected)
    return OperationResult(
        "added",
        repository=gateway.add_repository(selected.project_id, identity, repo_access_credential_id),
        project=selected,
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
