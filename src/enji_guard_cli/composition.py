"""Dependency wiring for the broad operator CLI and public client."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from enji_guard_cli.application import Application, AuthFacade, GitLabFacade, SubscriptionsFacade
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.client_facade import ClientQueryFacade
from enji_guard_cli.composition_support import create_read_surface
from enji_guard_cli.enji_gateway import GitLabGateway
from enji_guard_cli.enji_gateway.shared_client import create_shared_http_client
from enji_guard_cli.runtime_observability.telemetry import log_event
from enji_guard_cli.settings import default_settings


def create_application(auth_file: Path | None = None) -> Application:
    """Build every operator facade, including credential and mutating surfaces."""
    settings = default_settings()
    http_client = create_shared_http_client(settings, event_sink=log_event)
    try:
        surface = create_read_surface(auth_file, http_client, settings)
        return Application(
            runner=surface.runner,
            catalog=surface.catalog,
            auth=AuthFacade(AuthSessionService(auth_file, http_client, settings=settings)),
            audit=surface.audit,
            subscriptions=SubscriptionsFacade(
                catalog=surface.catalog,
                gateway=surface.audit_gateway,
                targets=surface.targets,
                fanout=surface.fanout,
            ),
            portfolio=surface.portfolio,
            gitlab=GitLabFacade(GitLabGateway(auth_file, http_client, auth_port=surface.credential_reader)),
        )
    except BaseException:
        http_client.close()
        raise


@contextmanager
def client_query_facade(auth_file: Path | None = None) -> Iterator[ClientQueryFacade]:
    """Own the narrow read surface used by the public context-managed client."""
    settings = default_settings()
    http_client = create_shared_http_client(settings, event_sink=log_event)
    try:
        surface = create_read_surface(auth_file, http_client, settings)
    except BaseException:
        http_client.close()
        raise
    try:
        yield ClientQueryFacade(surface.runner, surface.portfolio, surface.audit)
    finally:
        surface.runner.close()


__all__ = ["client_query_facade", "create_application"]
