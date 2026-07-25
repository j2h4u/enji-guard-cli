"""The one place that runs a delivery action and translates its failures.

Every domain facade is plain typed coordination.  Exception translation, exit
codes, request-scoped catalog observation, and the composed transport
lifecycle live here so all facades share exactly one implementation.
"""

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from enji_guard_cli.application.errors import ApplicationAuthError, ApplicationCommandError, exit_code_for_error
from enji_guard_cli.audit.artifacts import AuditArtifactUnavailableError
from enji_guard_cli.audit.errors import AuditMalformedError, AuditNotFoundError, AuditUpstreamError
from enji_guard_cli.audit.ports import AuditCatalogResult, MalformedAuditSnapshotError
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.portfolio.errors import PortfolioMalformedError, PortfolioNotFoundError, PortfolioUpstreamError


@dataclass(frozen=True, slots=True)
class ApplicationCatalogChange:
    action_key: str
    changed_fields: tuple[str, ...]
    kind: str


@dataclass(frozen=True, slots=True)
class ApplicationResult:
    payload: object
    catalog_changes: tuple[ApplicationCatalogChange, ...] = ()


class ApplicationLifecyclePort(Protocol):
    """Narrow lifecycle seam owned by application composition."""

    def close(self) -> None: ...


def _catalog_result_context() -> ContextVar[AuditCatalogResult | None]:
    return ContextVar("application_catalog_result", default=None)


@dataclass(frozen=True, slots=True)
class CatalogObservationScope:
    """Request-scoped hand-off of catalog observation from Audit to delivery.

    The catalog is fetched deep inside a use-case but reported next to the
    command result, and concurrent commands must not see each other's
    observation, so the hand-off is a context variable rather than state.
    """

    _current: ContextVar[AuditCatalogResult | None] = field(default_factory=_catalog_result_context, repr=False)

    def start(self) -> None:
        self._current.set(None)

    def record(self, result: AuditCatalogResult) -> None:
        self._current.set(result)

    def observed(self) -> tuple[ApplicationCatalogChange, ...]:
        result = self._current.get()
        changes = () if result is None else result.changes
        return tuple(
            ApplicationCatalogChange(change.action_key, change.changed_fields, change.kind) for change in changes
        )


@dataclass(slots=True)
class ApplicationRunner:
    """Execute one delivery action and own the composed transport lifecycle."""

    catalog_scope: CatalogObservationScope
    lifecycle: ApplicationLifecyclePort
    credential_location: Path
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        """Release composition-owned resources; safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        self.lifecycle.close()

    def execute(self, action: Callable[[], object]) -> ApplicationResult:
        """Execute one delivery action and translate context failures."""
        if self._closed:
            raise RuntimeError("application is closed")
        self.catalog_scope.start()
        try:
            payload = action()
        except EnjiApiError as exc:
            raise self._command_error(exc.code, exc.message) from exc
        except ApplicationAuthError as exc:
            raise self._command_error(exc.code, exc.message) from exc
        except (AuditArtifactUnavailableError, AuditNotFoundError, PortfolioNotFoundError) as exc:
            raise ApplicationCommandError("NOT_FOUND", str(exc), 4) from exc
        except (
            MalformedAuditSnapshotError,
            AuditMalformedError,
            AuditUpstreamError,
            PortfolioMalformedError,
            PortfolioUpstreamError,
        ) as exc:
            raise ApplicationCommandError("UPSTREAM", str(exc)) from exc
        except OSError as exc:
            raise ApplicationCommandError("STORAGE", str(exc)) from exc
        except ValueError as exc:
            raise ApplicationCommandError("VALIDATION", str(exc)) from exc
        return ApplicationResult(payload, self.catalog_scope.observed())

    def _command_error(self, code: str, message: str) -> ApplicationCommandError:
        """Translate one context failure, making credential failures actionable."""
        if code.startswith("AUTH_"):
            message = f"{message}. {self._auth_remediation()}"
        return ApplicationCommandError(code, message, exit_code_for_error(code))

    def _auth_remediation(self) -> str:
        """Name the credential file and the exact commands that repair first run."""
        return (
            f"Credential file: {self.credential_location}. "
            "First run: mkdir -p ~/.config/enji-guard/logs && chmod 700 ~/.config/enji-guard, then import a "
            "credential with: printf '%s' \"$ENJI_API_TOKEN\" | enji-guard auth import-bearer --stdin "
            "(cookie auth: enji-guard auth import-cookie --stdin). "
            "Verify with: enji-guard auth status"
        )


__all__ = [
    "ApplicationCatalogChange",
    "ApplicationLifecyclePort",
    "ApplicationResult",
    "ApplicationRunner",
    "CatalogObservationScope",
]
