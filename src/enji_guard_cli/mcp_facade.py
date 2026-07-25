"""Narrow read-only application surface owned by MCP delivery."""

from dataclasses import dataclass

from enji_guard_cli.application import ApplicationResult, ApplicationRunner, AuditFacade, PortfolioFacade
from enji_guard_cli.settings import RepositorySortName

type McpQueryResult = ApplicationResult


@dataclass(frozen=True, slots=True)
class McpQueryFacade:
    """Expose only the two curated MCP query scenarios."""

    _runner: ApplicationRunner
    _portfolio: PortfolioFacade
    _audit: AuditFacade

    def portfolio_overview(self, project: str | None, sort: RepositorySortName) -> ApplicationResult:
        return self._runner.execute(lambda: self._portfolio.portfolio_overview(project, sort))

    def repository_audits(self, repo: str, project: str | None) -> ApplicationResult:
        return self._runner.execute(lambda: self._audit.audit_read(repo, project=project, all_audits=True))


__all__ = ["McpQueryFacade", "McpQueryResult"]
