"""Composition owned exclusively by the long-lived MCP service."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from enji_guard_cli.auth_session import CredentialError, api_key_from_environment
from enji_guard_cli.composition_support import create_read_surface
from enji_guard_cli.enji_gateway.shared_client import create_shared_http_client
from enji_guard_cli.mcp_facade import McpQueryFacade
from enji_guard_cli.runtime_observability.auth_coordinator import RuntimeAuthCoordinatorAdapter
from enji_guard_cli.runtime_observability.telemetry import log_event, persist_event
from enji_guard_cli.settings import default_settings


class ServiceCredentialConfigurationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def api_key_auth_configured() -> bool:
    """Validate API-key ingress and select service auth ownership."""
    try:
        return api_key_from_environment() is not None
    except CredentialError as exc:
        raise ServiceCredentialConfigurationError(exc.code, exc.message) from exc


@contextmanager
def mcp_query_facade(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
    """Own the curated read-only MCP query surface for the server lifespan."""
    settings = default_settings()
    http_client = create_shared_http_client(settings, event_sink=log_event)
    try:
        surface = create_read_surface(auth_file, http_client, settings)
    except BaseException:
        http_client.close()
        raise
    try:
        yield McpQueryFacade(surface.runner, surface.portfolio, surface.audit)
    finally:
        surface.runner.close()


@contextmanager
def runtime_auth_service(auth_file: Path | None = None) -> Iterator[RuntimeAuthCoordinatorAdapter]:
    """Own only the credential coordinator the long-lived service supervises."""
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


__all__ = [
    "ServiceCredentialConfigurationError",
    "api_key_auth_configured",
    "mcp_query_facade",
    "runtime_auth_service",
]
