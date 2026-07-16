"""Project CRUD use-cases with repeat-safe outcomes."""

from enji_guard_cli.portfolio.errors import PortfolioNotFoundError
from enji_guard_cli.portfolio.models import OperationResult
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort
from enji_guard_cli.portfolio.selectors import resolve_project, validated_project_name


def create_project(name: str, *, gateway: PortfolioGatewayPort) -> OperationResult:
    project_name = validated_project_name(name)
    existing = next(
        (item for item in gateway.list_projects() if item.name and item.name.casefold() == project_name.casefold()),
        None,
    )
    if existing is not None:
        return OperationResult("already_present", project=existing)
    return OperationResult("created", project=gateway.create_project(project_name))


def rename_project(project: str, name: str, *, gateway: PortfolioGatewayPort) -> OperationResult:
    selected = resolve_project(gateway.list_projects(), project)
    new_name = validated_project_name(name)
    if selected.name is not None and selected.name.casefold() == new_name.casefold():
        return OperationResult("unchanged", project=selected)
    return OperationResult("renamed", project=gateway.rename_project(selected.project_id, new_name))


def delete_project(project: str, *, gateway: PortfolioGatewayPort) -> OperationResult:
    try:
        selected = resolve_project(gateway.list_projects(), project)
    except PortfolioNotFoundError:
        return OperationResult("already_absent", message=f"project already absent: {project}")
    detail = gateway.project_detail(selected.project_id)
    if detail.repositories:
        raise ValueError(f"project is not empty: {len(detail.repositories)} repo(s)")
    gateway.delete_project(selected.project_id)
    return OperationResult("deleted", project=selected)
