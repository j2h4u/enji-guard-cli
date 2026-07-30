"""Composition owned exclusively by the long-lived MCP service."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from enji_guard_cli.composition_support import create_read_surface
from enji_guard_cli.enji_gateway.shared_client import create_shared_http_client
from enji_guard_cli.mcp_facade import McpQueryFacade
from enji_guard_cli.runtime_observability.auth_coordinator import RuntimeAuthCoordinatorAdapter
from enji_guard_cli.runtime_observability.telemetry import log_event, persist_event
from enji_guard_cli.settings import default_settings


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


__all__ = ["mcp_query_facade", "runtime_auth_service"]
