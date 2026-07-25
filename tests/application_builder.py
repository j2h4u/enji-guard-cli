"""Explicit test construction of the application facades.

Every collaborator a facade needs is required in production, so tests supply
one too.  This module keeps the stub-only collaborators and the CLI's facade
routing in a single place instead of repeating them at every call site.
"""

from dataclasses import dataclass, field
from typing import cast

from enji_guard_cli.application import (
    Application,
    ApplicationLifecyclePort,
    ApplicationRunner,
    AuditCatalogService,
    AuditFacade,
    AuditProjectSource,
    AuthFacade,
    CatalogObservationScope,
    GitLabFacade,
    PortfolioFacade,
    SubscriptionsFacade,
)
from enji_guard_cli.audit.catalog_observation import AuditCatalogObservationPort
from enji_guard_cli.audit.ports import AuditCatalogResult, AuditGatewayPort, AuditLedgerPort, AuditRun
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.gitlab.ports import GitLabDiscoveryPort
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort, PortfolioTargetService
from enji_guard_cli.runtime_observability.ports import RuntimeAuthCoordinator
from enji_guard_cli.settings import default_settings


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
    """Collaborators for one facade tree under test; unset ones stay inert."""

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
        """Wire the same facade tree production composition wires."""
        audit_gateway = cast(AuditGatewayPort, self.audit_gateway)
        portfolio_gateway = cast(PortfolioGatewayPort, self.portfolio_gateway)
        targets = cast(PortfolioTargetService, self.target_service)
        fanout = BoundedFanout(default_settings().fanout)
        scope = CatalogObservationScope()
        catalog = AuditCatalogService(audit_gateway, cast(AuditCatalogObservationPort, self.catalog_observer), scope)
        audit = AuditFacade(
            catalog=catalog,
            gateway=audit_gateway,
            ledger=cast(AuditLedgerPort, self.ledger),
            targets=targets,
            project_source=AuditProjectSource(portfolio_gateway),
            fanout=fanout,
        )
        return Application(
            runner=ApplicationRunner(scope, cast(ApplicationLifecyclePort, self.lifecycle)),
            catalog=catalog,
            auth=AuthFacade(cast(AuthSessionService, self.auth), cast(RuntimeAuthCoordinator, self.runtime_auth)),
            audit=audit,
            subscriptions=SubscriptionsFacade(catalog=catalog, gateway=audit_gateway, targets=targets, fanout=fanout),
            portfolio=PortfolioFacade(
                gateway=portfolio_gateway, targets=targets, catalog=catalog, audits=audit, fanout=fanout
            ),
            gitlab=GitLabFacade(cast(GitLabDiscoveryPort, self.gitlab_gateway)),
        )


@dataclass(frozen=True, slots=True)
class FacadeRouter:
    """Serve one flat CLI fake under every facade attribute the CLI reads.

    CLI command handlers name the facade they use (``.audit``, ``.portfolio``,
    ...).  A CLI test cares about which method was called, not which facade
    owns it, so one recording fake answers for all of them.
    """

    target: object

    @property
    def runner(self) -> object:
        return self.target

    @property
    def catalog(self) -> object:
        return self.target

    @property
    def auth(self) -> object:
        return self.target

    @property
    def audit(self) -> object:
        return self.target

    @property
    def subscriptions(self) -> object:
        return self.target

    @property
    def portfolio(self) -> object:
        return self.target

    @property
    def gitlab(self) -> object:
        return self.target
