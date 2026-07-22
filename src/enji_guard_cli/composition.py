"""Dependency wiring for product delivery surfaces."""

from pathlib import Path

from enji_guard_cli.application import Application
from enji_guard_cli.audit.catalog_observation import AuditCatalogObserver
from enji_guard_cli.audit.ledger import FileAuditLedger
from enji_guard_cli.auth_session.adapters import GatewayCredentialReader, RuntimeAuthCoordinator
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.enji_gateway import AuditGateway, GitLabGateway, PortfolioGateway
from enji_guard_cli.enji_gateway.pooled_client import PooledEnjiHttpClient
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.mcp_facade import McpQueryFacade
from enji_guard_cli.portfolio.selectors import GatewayPortfolioTargetService
from enji_guard_cli.settings import default_settings


def create_application(auth_file: Path | None = None) -> Application:
    """Build the broad operator application facade."""
    settings = default_settings()
    ledger = FileAuditLedger(
        settings.active_run_ledger.state_file,
        ttl_seconds=settings.active_run_ledger.ttl_seconds,
        lookup_grace_seconds=settings.active_run_ledger.lookup_grace_seconds,
    )
    credential_reader = GatewayCredentialReader(auth_file, settings=settings)
    runtime_auth = RuntimeAuthCoordinator(auth_file, settings=settings)
    auth_service = AuthSessionService(auth_file, settings=settings)
    fanout = BoundedFanout(settings.fanout)
    pooled_client = PooledEnjiHttpClient(settings)
    try:
        portfolio_gateway = PortfolioGateway(auth_file, pooled_client, auth_port=credential_reader)
        return Application(
            audit_gateway=AuditGateway(auth_file, pooled_client, auth_port=credential_reader),
            portfolio_gateway=portfolio_gateway,
            gitlab_gateway=GitLabGateway(auth_file, pooled_client, auth_port=credential_reader),
            auth=auth_service,
            ledger=ledger,
            catalog_observer=AuditCatalogObserver(settings.audit_catalog.state_file),
            target_service=GatewayPortfolioTargetService(portfolio_gateway, fanout),
            runtime_auth=runtime_auth,
            fanout=fanout,
            lifecycle=pooled_client,
        )
    except BaseException:
        pooled_client.close()
        raise


def create_mcp_query_facade(auth_file: Path | None = None) -> McpQueryFacade:
    """Build the curated read-only MCP query surface."""
    return McpQueryFacade(create_application(auth_file))


__all__ = ["create_application", "create_mcp_query_facade"]
