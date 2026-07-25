"""The composed MCP query surface must always be closeable.

The facade is deliberately narrow, so nothing but composition can reach the
application (and its pooled HTTP client) underneath it.
"""

import asyncio
import importlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import cast

import pytest
from mcp.server.fastmcp import FastMCP
from typer.testing import CliRunner

import enji_guard_cli.composition as composition_module
import enji_guard_cli.delivery.mcp.server as server_module
from enji_guard_cli.composition import mcp_query_facade
from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.delivery.mcp.server import create_mcp_server, run_mcp_server_async
from enji_guard_cli.mcp_facade import McpQueryFacade
from enji_guard_cli.runtime_observability.auth_coordinator import RuntimeAuthCoordinatorAdapter
from enji_guard_cli.runtime_observability.supervisor import McpServerFactory
from enji_guard_cli.transport import EnjiHttpRequest, HttpxEnjiHttpClient

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")


def _owned_http_client(queries: McpQueryFacade) -> HttpxEnjiHttpClient:
    """The pooled client the composed application owns and must release."""
    client = queries._runner.lifecycle
    assert isinstance(client, HttpxEnjiHttpClient)
    return client


@asynccontextmanager
async def _running_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Drive the server lifespan exactly as a transport run loop would."""
    mcp_server = server._mcp_server
    async with mcp_server.lifespan(mcp_server):
        yield


def test_scoped_query_facade_closes_its_application_and_pool(tmp_path: Path) -> None:
    with mcp_query_facade(tmp_path / "auth.json") as queries:
        runner = queries._runner
        client = _owned_http_client(queries)
        assert client.is_closed is False

    assert client.is_closed is True
    # The pool is genuinely released, not merely flagged: httpx refuses to send
    # on a closed client, so this cannot pass if close() were skipped.
    with pytest.raises(RuntimeError, match="client has been closed"):
        client.request_blocking(
            EnjiHttpRequest(
                method="GET",
                url="http://127.0.0.1:1/unreachable",
                operation="probe",
                headers={},
            )
        )
    with pytest.raises(RuntimeError, match="application is closed"):
        runner.execute(lambda: None)


def test_creating_the_server_composes_nothing_before_the_lifespan_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        del auth_file
        raise AssertionError("creating the server must not compose a query surface")

    monkeypatch.setattr(server_module, "mcp_query_facade", refuse)

    server = create_mcp_server()

    assert server.name == "enji-guard-cli"


def test_server_lifespan_releases_the_pool_it_composed(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the real lifespan must leave no open connection pool behind."""
    captured: list[HttpxEnjiHttpClient] = []

    @contextmanager
    def recording_facade(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        """Record the real composed client without changing ownership."""
        with mcp_query_facade(auth_file) as facade:
            captured.append(_owned_http_client(facade))
            yield facade

    monkeypatch.setattr(server_module, "mcp_query_facade", recording_facade)
    server = create_mcp_server()

    async def exercise() -> None:
        async with _running_lifespan(server):
            assert len(captured) == 1
            assert captured[0].is_closed is False

    asyncio.run(exercise())

    assert captured[0].is_closed is True


def test_server_lifespan_owns_and_closes_the_composed_query_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    facade = cast(McpQueryFacade, object())
    opened: list[McpQueryFacade] = []
    closed: list[McpQueryFacade] = []

    @contextmanager
    def scoped_facade(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        del auth_file
        opened.append(facade)
        try:
            yield facade
        finally:
            closed.append(facade)

    monkeypatch.setattr(server_module, "mcp_query_facade", scoped_facade)
    server = create_mcp_server()

    async def exercise() -> None:
        async with _running_lifespan(server):
            assert opened == [facade]
            assert closed == []

    asyncio.run(exercise())

    assert closed == [facade]


def _run_service_kwargs(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> dict[str, object]:
    """Invoke the real container entrypoint, capturing what it hands the supervisor."""
    captured: dict[str, object] = {}

    def fake_run_service(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "run_service", fake_run_service)
    result = CliRunner().invoke(app, argv)

    assert result.exit_code == 0, result.output
    return captured


def test_the_run_entrypoint_composes_the_narrow_surface_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`enji-guard run` is the container entrypoint; it must take the narrow path.

    Previously `run` built the whole mutating application and injected it, so
    the lifespan short-circuited and this composition never ran in production.
    """
    composed: list[HttpxEnjiHttpClient] = []

    @contextmanager
    def recording_facade(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        with mcp_query_facade(auth_file) as facade:
            composed.append(_owned_http_client(facade))
            yield facade

    monkeypatch.setattr(server_module, "mcp_query_facade", recording_facade)

    captured = _run_service_kwargs(monkeypatch, ["run", "--transport", "stdio"])
    factory = cast(McpServerFactory, captured["mcp_server_factory"])
    server = cast(FastMCP, factory("127.0.0.1", 18081))

    # Building the server must not compose anything; the lifespan owns it.
    assert composed == []

    async def exercise() -> None:
        async with _running_lifespan(server):
            assert len(composed) == 1
            assert composed[0].is_closed is False

    asyncio.run(exercise())

    assert composed[0].is_closed is True


def test_the_run_entrypoint_never_hands_mcp_a_mutating_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service must not build the auth-import/GitLab/subscription facades."""

    def refuse(auth_file: Path | None = None) -> object:
        del auth_file
        raise AssertionError("the MCP service must not compose the full application")

    monkeypatch.setattr(cli_module, "create_application", refuse)

    captured = _run_service_kwargs(monkeypatch, ["run", "--transport", "stdio"])

    assert captured["mcp_server_runner"] is run_mcp_server_async
    assert isinstance(captured["runtime_auth"], RuntimeAuthCoordinatorAdapter)


def test_the_run_entrypoint_closes_the_credential_coordinator_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The auth client outlives the MCP surface but must still be released."""
    coordinators: list[RuntimeAuthCoordinatorAdapter] = []

    captured_client: list[HttpxEnjiHttpClient] = []
    real_service = composition_module.runtime_auth_service

    @contextmanager
    def recording_service(auth_file: Path | None = None) -> Iterator[RuntimeAuthCoordinatorAdapter]:
        with real_service(auth_file) as coordinator:
            coordinators.append(coordinator)
            client = coordinator.client
            assert isinstance(client, HttpxEnjiHttpClient)
            captured_client.append(client)
            assert client.is_closed is False
            yield coordinator

    monkeypatch.setattr(cli_module, "runtime_auth_service", recording_service)

    _run_service_kwargs(monkeypatch, ["run", "--transport", "stdio"])

    assert len(coordinators) == 1
    assert captured_client[0].is_closed is True


def test_the_credential_pool_is_released_when_the_supervisor_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure must not orphan the pool the entrypoint composed."""
    captured_client: list[HttpxEnjiHttpClient] = []
    real_service = composition_module.runtime_auth_service

    @contextmanager
    def recording_service(auth_file: Path | None = None) -> Iterator[RuntimeAuthCoordinatorAdapter]:
        with real_service(auth_file) as coordinator:
            client = coordinator.client
            assert isinstance(client, HttpxEnjiHttpClient)
            captured_client.append(client)
            yield coordinator

    def exploding_run_service(**kwargs: object) -> None:
        del kwargs
        raise RuntimeError("supervisor failed")

    monkeypatch.setattr(cli_module, "runtime_auth_service", recording_service)
    monkeypatch.setattr(cli_module, "run_service", exploding_run_service)

    result = CliRunner().invoke(app, ["run", "--transport", "stdio"])

    assert result.exit_code != 0
    assert captured_client[0].is_closed is True

