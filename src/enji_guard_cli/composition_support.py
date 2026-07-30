"""Shared construction for the read-only application surfaces.

Both the public client and the MCP service read the same portfolio/audit
surface.  Keeping that construction here prevents either delivery root from
gaining the broad operator application by accident.
"""

from dataclasses import dataclass
from pathlib import Path

from enji_guard_cli.application import (
    ApplicationRunner,
    AuditCatalogService,
    AuditFacade,
    AuditProjectSource,
    CatalogObservationScope,
    PortfolioFacade,
)
from enji_guard_cli.audit.catalog_observation import AuditCatalogObserver
from enji_guard_cli.audit.ledger import FileAuditLedger
from enji_guard_cli.auth_session.adapters import StoredCredentialReader
from enji_guard_cli.enji_gateway import AuditGateway, PortfolioGateway
from enji_guard_cli.enji_gateway.shared_client import SharedHttpClient
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.portfolio.selectors import GatewayPortfolioTargetService
from enji_guard_cli.settings import EnjiGuardSettings


@dataclass(frozen=True, slots=True)
class ReadSurface:
    """Read facades plus the collaborator reused by the operator composition."""

    runner: ApplicationRunner
    catalog: AuditCatalogService
    audit: AuditFacade
    portfolio: PortfolioFacade
    credential_reader: StoredCredentialReader
    audit_gateway: AuditGateway
    targets: GatewayPortfolioTargetService
    fanout: BoundedFanout


def create_read_surface(
    auth_file: Path | None, http_client: SharedHttpClient, settings: EnjiGuardSettings
) -> ReadSurface:
    """Wire portfolio and audit reads around one caller-owned HTTP client."""
    credential_reader = StoredCredentialReader(auth_file, settings=settings)
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
    return ReadSurface(
        runner=ApplicationRunner(scope, http_client),
        catalog=catalog,
        audit=audit,
        portfolio=PortfolioFacade(
            gateway=portfolio_gateway, targets=targets, catalog=catalog, audits=audit, fanout=fanout
        ),
        credential_reader=credential_reader,
        audit_gateway=audit_gateway,
        targets=targets,
        fanout=fanout,
    )


__all__ = ["ReadSurface", "create_read_surface"]
