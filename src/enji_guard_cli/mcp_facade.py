"""Narrow read-only application surface owned by MCP delivery."""

from dataclasses import dataclass

from enji_guard_cli.application import Application, ApplicationResult
from enji_guard_cli.settings import RepositorySortName

type McpQueryResult = ApplicationResult


@dataclass(frozen=True, slots=True)
class McpQueryFacade:
    """Expose only the two curated MCP query scenarios."""

    _application: Application

    def portfolio_overview(self, project: str | None, sort: RepositorySortName) -> ApplicationResult:
        return self._application.execute(lambda: self._application.portfolio_overview(project, sort))

    def repository_audits(self, repo: str, project: str | None) -> ApplicationResult:
        return self._application.execute(lambda: self._application.audit_read(repo, project=project, all_audits=True))


__all__ = ["McpQueryFacade", "McpQueryResult"]
