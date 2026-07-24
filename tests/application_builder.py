"""Explicit test construction of the application facade.

Every collaborator the facade needs is required in production, so tests supply
one too.  This builder keeps the stub-only collaborators in a single place
instead of repeating them at every construction site.
"""

from dataclasses import dataclass, field
from typing import cast

from enji_guard_cli.application import Application, ApplicationLifecyclePort
from enji_guard_cli.audit.catalog_observation import AuditCatalogObservationPort
from enji_guard_cli.audit.ports import AuditCatalogResult, AuditGatewayPort, AuditLedgerPort, AuditRun
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.gitlab.ports import GitLabDiscoveryPort
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort, PortfolioTargetService
from enji_guard_cli.runtime_observability.ports import RuntimeAuthCoordinator


class UnobservedCatalog:
    """Catalog observation that reports exactly what the gateway returned."""

    def observe(self, result: AuditCatalogResult) -> AuditCatalogResult:
        return result


class PassthroughLedger:
    """Ledger that keeps upstream active runs exactly as reported."""

    def reconcile(self, _repo_id: str, upstream: tuple[AuditRun, ...], _task_detail: object) -> tuple[AuditRun, ...]:
        return upstream


class RecordingLifecycle:
    """Lifecycle seam that records how often composition released it."""

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


@dataclass(frozen=True, slots=True)
class ApplicationStubs:
    """Collaborators for one facade under test; unset ones stay inert stubs."""

    audit_gateway: object = field(default_factory=object)
    portfolio_gateway: object = field(default_factory=object)
    auth: object = field(default_factory=object)
    ledger: object = field(default_factory=PassthroughLedger)
    catalog_observer: object = field(default_factory=UnobservedCatalog)
    target_service: object = field(default_factory=object)
    runtime_auth: object = field(default_factory=object)
    gitlab_gateway: object = field(default_factory=object)
    lifecycle: object = field(default_factory=RecordingLifecycle)

    def build(self) -> Application:
        return Application(
            audit_gateway=cast(AuditGatewayPort, self.audit_gateway),
            portfolio_gateway=cast(PortfolioGatewayPort, self.portfolio_gateway),
            auth=cast(AuthSessionService, self.auth),
            ledger=cast(AuditLedgerPort, self.ledger),
            catalog_observer=cast(AuditCatalogObservationPort, self.catalog_observer),
            target_service=cast(PortfolioTargetService, self.target_service),
            runtime_auth=cast(RuntimeAuthCoordinator, self.runtime_auth),
            gitlab_gateway=cast(GitLabDiscoveryPort, self.gitlab_gateway),
            lifecycle=cast(ApplicationLifecyclePort, self.lifecycle),
        )
