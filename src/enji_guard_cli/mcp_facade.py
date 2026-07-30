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

    def repository_audits(
        self, repo: str, project: str | None, audit_selectors: list[str] | None = None
    ) -> ApplicationResult:
        """Return compact summaries unless the caller names report bodies."""
        selectors = [selector.strip().removeprefix("audit.") for selector in audit_selectors or () if selector.strip()]
        if not selectors:
            return self._runner.execute(lambda: self._audit.audit_summary(repo, project=project))
        return self._runner.execute(lambda: self._audit.audit_read(repo, selectors, project=project, all_audits=False))


__all__ = ["McpQueryFacade", "McpQueryResult"]
