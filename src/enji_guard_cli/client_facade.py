"""Internal, narrow read facade used by the public Python client."""

from collections.abc import Callable
from dataclasses import dataclass

from enji_guard_cli.application import ApplicationCommandError, ApplicationRunner, AuditFacade, PortfolioFacade
from enji_guard_cli.settings import RepositorySortName


@dataclass(frozen=True, slots=True)
class ClientQueryCatalogChange:
    """Internal catalog observation with no application DTO leakage."""

    action_key: str
    changed_fields: tuple[str, ...]
    kind: str


@dataclass(frozen=True, slots=True)
class ClientQueryResult:
    """Internal query result that keeps application DTOs behind this facade."""

    payload: object
    catalog_observed: bool
    catalog_changes: tuple[ClientQueryCatalogChange, ...]


class ClientQueryError(Exception):
    """Internal stable failure passed from the facade to the public client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ClientQueryFacade:
    """Expose the supported read queries without leaking the full application."""

    _runner: ApplicationRunner
    _portfolio: PortfolioFacade
    _audit: AuditFacade

    def portfolio_overview(self, project: str | None, sort: RepositorySortName) -> ClientQueryResult:
        return self._execute(lambda: self._portfolio.portfolio_overview(project, sort))

    def repository_status(self, repository: str, project: str | None) -> ClientQueryResult:
        return self._execute(lambda: self._portfolio.repository_status(repository, project))

    def audit_summary(self, repository: str, project: str | None) -> ClientQueryResult:
        return self._execute(lambda: self._audit.audit_summary(repository, project=project))

    def audit_read(self, repository: str, audits: list[str], project: str | None) -> ClientQueryResult:
        return self._execute(lambda: self._audit.audit_read(repository, audits, project=project, all_audits=False))

    def _execute(self, action: Callable[[], object]) -> ClientQueryResult:
        try:
            result = self._runner.execute(action)
        except ApplicationCommandError as exc:
            raise ClientQueryError(exc.code, exc.message) from None
        return ClientQueryResult(
            result.payload,
            result.catalog_observed,
            tuple(
                ClientQueryCatalogChange(change.action_key, change.changed_fields, change.kind)
                for change in result.catalog_changes
            ),
        )


__all__ = ["ClientQueryCatalogChange", "ClientQueryError", "ClientQueryFacade", "ClientQueryResult"]
