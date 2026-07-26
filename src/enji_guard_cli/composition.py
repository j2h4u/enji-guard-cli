"""Dependency wiring for product delivery surfaces."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from enji_guard_cli.application import (
    Application,
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
from enji_guard_cli.audit.catalog_observation import AuditCatalogObserver
from enji_guard_cli.audit.ledger import FileAuditLedger
from enji_guard_cli.auth_session.adapters import GatewayCredentialReader
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.enji_gateway import AuditGateway, GitLabGateway, PortfolioGateway
from enji_guard_cli.enji_gateway.shared_client import SharedHttpClient, create_shared_http_client
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.mcp_facade import McpQueryFacade
from enji_guard_cli.portfolio.selectors import GatewayPortfolioTargetService
from enji_guard_cli.runtime_observability.auth_coordinator import RuntimeAuthCoordinatorAdapter
from enji_guard_cli.runtime_observability.telemetry import log_event, persist_event
from enji_guard_cli.settings import EnjiGuardSettings, default_settings


@dataclass(frozen=True, slots=True)
class _ReadSurface:
    """The read facades, plus the collaborators a mutating surface reuses."""

    runner: ApplicationRunner
    catalog: AuditCatalogService
    audit: AuditFacade
    portfolio: PortfolioFacade
    subscriptions: SubscriptionsFacade
    credential_reader: GatewayCredentialReader


def _create_read_surface(
    auth_file: Path | None, http_client: SharedHttpClient, settings: EnjiGuardSettings
) -> _ReadSurface:
    """Wire the facades that read portfolio membership and audit state."""
    credential_reader = GatewayCredentialReader(auth_file, settings=settings)
    fanout = BoundedFanout(settings.fanout)
    ledger = FileAuditLedger(
        settings.active_run_ledger.state_file,
        ttl_seconds=settings.active_run_ledger.ttl_seconds,
        lookup_grace_seconds=settings.active_run_ledger.lookup_grace_seconds,
    )
    audit_gateway = AuditGateway(auth_file, http_client, auth_port=credential_reader)
    portfolio_gateway = PortfolioGateway(auth_file, http_client, auth_port=credential_reader)
    targets = GatewayPortfolioTargetService(portfolio_gateway, fanout)
    scope = CatalogObservationScope()
    catalog = AuditCatalogService(audit_gateway, AuditCatalogObserver(settings.audit_catalog.state_file), scope)
    audit = AuditFacade(
        catalog=catalog,
        gateway=audit_gateway,
        ledger=ledger,
        targets=targets,
        project_source=AuditProjectSource(portfolio_gateway),
        fanout=fanout,
    )
    return _ReadSurface(
        runner=ApplicationRunner(scope, http_client),
        catalog=catalog,
        audit=audit,
        portfolio=PortfolioFacade(
            gateway=portfolio_gateway, targets=targets, catalog=catalog, audits=audit, fanout=fanout
        ),
        subscriptions=SubscriptionsFacade(catalog=catalog, gateway=audit_gateway, targets=targets, fanout=fanout),
        credential_reader=credential_reader,
    )


def create_application(auth_file: Path | None = None) -> Application:
    """Build every operator facade, including credential and mutating surfaces."""
    settings = default_settings()
    http_client = create_shared_http_client(settings, event_sink=log_event)
    try:
        surface = _create_read_surface(auth_file, http_client, settings)
        runtime_auth = RuntimeAuthCoordinatorAdapter(
            auth_file,
            settings=settings,
            event_sink=log_event,
            outcome_sink=persist_event,
            client=http_client,
        )
        return Application(
            runner=surface.runner,
            catalog=surface.catalog,
            auth=AuthFacade(AuthSessionService(auth_file, http_client, settings=settings), runtime_auth),
            audit=surface.audit,
            subscriptions=surface.subscriptions,
            portfolio=surface.portfolio,
            gitlab=GitLabFacade(GitLabGateway(auth_file, http_client, auth_port=surface.credential_reader)),
        )
    except BaseException:
        http_client.close()
        raise


@contextmanager
def mcp_query_facade(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
    """Own the curated read-only MCP query surface for the caller's scope.

    MCP reads two scenarios, so it composes only the portfolio and audit
    facades — no credential import, no GitLab discovery, no mutating
    subscription writes.  Binding it to a scope keeps its pooled client
    closeable.
    """
    settings = default_settings()
    http_client = create_shared_http_client(settings, event_sink=log_event)
    try:
        surface = _create_read_surface(auth_file, http_client, settings)
    except BaseException:
        http_client.close()
        raise
    try:
        yield McpQueryFacade(surface.runner, surface.portfolio, surface.audit)
    finally:
        surface.runner.close()


@contextmanager
def runtime_auth_service(auth_file: Path | None = None) -> Iterator[RuntimeAuthCoordinatorAdapter]:
    """Own only the credential coordinator the long-lived service supervises.

    The service needs startup reconciliation and a background refresh loop, and
    nothing else from the operator application.  Composing just the coordinator
    keeps the mutating facades — auth import, GitLab discovery, subscription
    writes — out of a process whose only client-facing surface is read-only MCP.
    Its pooled client is independent of the one the MCP lifespan composes, so
    the two shut down on their own schedules.
    """
    settings = default_settings()
    http_client = create_shared_http_client(settings, event_sink=log_event)
    try:
        yield RuntimeAuthCoordinatorAdapter(
            auth_file,
            settings=settings,
            event_sink=log_event,
            outcome_sink=persist_event,
            client=http_client,
        )
    finally:
        http_client.close()


__all__ = ["create_application", "mcp_query_facade", "runtime_auth_service"]
