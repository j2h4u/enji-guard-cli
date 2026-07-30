"""Supported, read-only Python client for Enji Guard."""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self, cast

from enji_guard_cli.client_facade import ClientQueryError, ClientQueryFacade, ClientQueryResult
from enji_guard_cli.composition import client_query_facade
from enji_guard_cli.delivery.presentation import json_projection
from enji_guard_cli.json_types import JsonObjectPayload, JsonScalar, JsonValue
from enji_guard_cli.settings import RepositorySortName

type RepositorySort = RepositorySortName
type JsonObject = JsonObjectPayload


@dataclass(frozen=True, slots=True)
class ClientCatalogChange:
    """One provider catalog change observed while fulfilling a client query."""

    action_key: str
    changed_fields: tuple[str, ...]
    kind: str


@dataclass(frozen=True, slots=True)
class ClientResult:
    """Provider-neutral JSON data and the catalog observation made for it."""

    data: JsonObject
    catalog_observed: bool
    catalog_changes: tuple[ClientCatalogChange, ...]


class EnjiGuardError(Exception):
    """A stable client-facing application failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EnjiGuardClient:
    """A context-managed, read-only Enji Guard client.

    The client owns a pooled HTTP transport only while inside its context.
    """

    def __init__(self, auth_file: Path | None = None) -> None:
        self._auth_file = auth_file
        self._scope: AbstractContextManager[ClientQueryFacade] | None = None
        self._facade: ClientQueryFacade | None = None

    def __enter__(self) -> Self:
        if self._facade is not None:
            raise RuntimeError("EnjiGuardClient is already active")
        scope = client_query_facade(self._auth_file)
        self._facade = scope.__enter__()
        self._scope = scope
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        scope = self._scope
        self._facade = None
        self._scope = None
        if scope is not None:
            scope.__exit__(exc_type, exc_value, traceback)

    def portfolio_overview(self, project: str | None = None, sort: RepositorySort = "default") -> ClientResult:
        return self._execute(lambda facade: facade.portfolio_overview(project, sort))

    def repository_status(self, repository: str, project: str | None = None) -> ClientResult:
        return self._execute(lambda facade: facade.repository_status(repository, project))

    def audit_summary(self, repository: str, project: str | None = None) -> ClientResult:
        return self._execute(lambda facade: facade.audit_summary(repository, project))

    def audit_read(self, repository: str, audits: Sequence[str], project: str | None = None) -> ClientResult:
        if isinstance(audits, str):
            raise EnjiGuardError("VALIDATION", "pass audit selectors as a sequence, not a string")
        selectors = _selectors(audits)
        if not selectors:
            raise EnjiGuardError("VALIDATION", "pass at least one non-empty audit selector")
        return self._execute(lambda facade: facade.audit_read(repository, selectors, project))

    def _execute(self, query: Callable[[ClientQueryFacade], ClientQueryResult]) -> ClientResult:
        facade = self._facade
        if facade is None:
            raise RuntimeError("EnjiGuardClient must be used as a context manager")
        try:
            result = query(facade)
        except ClientQueryError as exc:
            raise EnjiGuardError(exc.code, exc.message) from None
        rendered = json_projection(result.payload)
        data: JsonObject = (
            cast(JsonObject, rendered) if isinstance(rendered, dict) else {"items": cast(JsonValue, rendered)}
        )
        return ClientResult(
            data=data,
            catalog_observed=result.catalog_observed,
            catalog_changes=tuple(
                ClientCatalogChange(change.action_key, change.changed_fields, change.kind)
                for change in result.catalog_changes
            ),
        )


def _selectors(audits: Sequence[str]) -> list[str]:
    return [normalized for selector in audits if (normalized := selector.strip().removeprefix("audit."))]


__all__ = [
    "ClientCatalogChange",
    "ClientResult",
    "EnjiGuardClient",
    "EnjiGuardError",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "RepositorySort",
]
