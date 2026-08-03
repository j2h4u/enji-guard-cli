"""The composed MCP query surface must always be closeable.

The facade is deliberately narrow, so nothing but composition can reach the
application (and its pooled HTTP client) underneath it.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Never, cast

import pytest
import typer
from mcp.server.fastmcp import FastMCP

import enji_guard_cli.composition as composition_module
import enji_guard_cli.composition_support as composition_support_module
import enji_guard_cli.delivery.mcp.server as server_module
import enji_guard_cli.delivery.service as service_module
import enji_guard_cli.service_composition as service_composition_module
from enji_guard_cli.composition import client_query_facade
from enji_guard_cli.delivery.cli.app import _application, _close_cached_application, _state
from enji_guard_cli.delivery.mcp.server import create_mcp_server
from enji_guard_cli.mcp_facade import McpQueryFacade
from enji_guard_cli.runtime_observability.supervisor import McpServerFactory
from enji_guard_cli.service_composition import mcp_query_facade
from enji_guard_cli.transport import EnjiHttpRequest, HttpxEnjiHttpClient


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


def test_read_compositions_never_construct_subscriptions_facades(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("read-only compositions must not construct SubscriptionsFacade")

    monkeypatch.setattr(composition_module, "SubscriptionsFacade", refuse)
    monkeypatch.setattr(composition_support_module, "SubscriptionsFacade", refuse, raising=False)

    with client_query_facade(tmp_path / "client.json"):
        pass
    with mcp_query_facade(tmp_path / "mcp.json"):
        pass


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


def test_service_root_composes_only_narrow_mcp_and_closes_runtime_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    coordinators: list[HttpxEnjiHttpClient] = []
    mcp_clients: list[HttpxEnjiHttpClient] = []
    real_runtime_auth = service_composition_module.runtime_auth_service

    @contextmanager
    def recording_runtime_auth(auth_file: Path | None = None) -> Iterator[object]:
        with real_runtime_auth(auth_file) as coordinator:
            client = coordinator.client
            assert isinstance(client, HttpxEnjiHttpClient)
            coordinators.append(client)
            yield coordinator

    @contextmanager
    def recording_mcp_facade(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        with mcp_query_facade(auth_file) as facade:
            mcp_clients.append(_owned_http_client(facade))
            yield facade

    def fake_run_service(**kwargs: object) -> None:
        captured.update(kwargs)

    def refuse_application(_auth_file: Path | None = None) -> object:
        raise AssertionError("the service must not compose the mutating operator application")

    monkeypatch.setattr(service_module, "runtime_auth_service", recording_runtime_auth)
    monkeypatch.setattr(service_module, "run_service", fake_run_service)
    monkeypatch.setattr(server_module, "mcp_query_facade", recording_mcp_facade)
    monkeypatch.setattr(composition_module, "create_application", refuse_application)

    service_module.run(service_module.RuntimeServiceOptions(transport="stdio", host="127.0.0.1", port=18081))

    assert coordinators[0].is_closed is True
    factory = cast(McpServerFactory, captured["mcp_server_factory"])
    server = cast(FastMCP, factory("127.0.0.1", 18081))
    assert mcp_clients == []

    async def exercise() -> None:
        async with _running_lifespan(server):
            assert mcp_clients[0].is_closed is False

    asyncio.run(exercise())
    assert mcp_clients[0].is_closed is True


def test_service_root_closes_runtime_pool_when_supervisor_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[HttpxEnjiHttpClient] = []
    real_runtime_auth = service_composition_module.runtime_auth_service

    @contextmanager
    def recording_runtime_auth(auth_file: Path | None = None) -> Iterator[object]:
        with real_runtime_auth(auth_file) as coordinator:
            client = coordinator.client
            assert isinstance(client, HttpxEnjiHttpClient)
            clients.append(client)
            yield coordinator

    def fail_supervisor(**_kwargs: object) -> None:
        raise RuntimeError("supervisor failed")

    monkeypatch.setattr(service_module, "runtime_auth_service", recording_runtime_auth)
    monkeypatch.setattr(service_module, "run_service", fail_supervisor)

    with pytest.raises(RuntimeError, match="supervisor failed"):
        service_module.run(service_module.RuntimeServiceOptions(transport="stdio", host="127.0.0.1", port=18081))

    assert clients[0].is_closed is True


def test_api_key_service_bypasses_cookie_runtime_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def refuse_runtime_auth(_auth_file: Path | None = None) -> Never:
        raise AssertionError("API-key mode must not compose cookie refresh")

    monkeypatch.setenv("ENJI_GUARD_API_KEY", "secret-api-key")
    monkeypatch.setattr(service_module, "runtime_auth_service", refuse_runtime_auth)
    monkeypatch.setattr(service_module, "api_key_auth_configured", lambda: True)
    monkeypatch.setattr(service_module, "run_service", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(service_module, "_mcp_implementation", lambda: (lambda *_args, **_kwargs: object(), object()))

    service_module.run(service_module.RuntimeServiceOptions(transport="stdio", host="127.0.0.1", port=18081))

    assert "runtime_auth" not in captured


def test_api_key_service_reports_configuration_errors_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ENJI_GUARD_API_KEY", "contains whitespace")
    monkeypatch.delenv("ENJI_GUARD_API_KEY_FILE", raising=False)
    monkeypatch.setattr(service_module, "_mcp_implementation", lambda: (lambda *_args, **_kwargs: object(), object()))

    with pytest.raises(typer.Exit) as caught:
        service_module.run(service_module.RuntimeServiceOptions(transport="stdio", host="127.0.0.1", port=18081))

    assert caught.value.exit_code == 2
    assert capsys.readouterr().err == "API_KEY_INVALID: the Enji API key is empty or contains whitespace\n"


def test_switching_auth_file_closes_the_displaced_application(tmp_path: Path) -> None:
    """The CLI cache holds one application; the displaced one must be closed.

    A single invocation legitimately asks twice: `_run` resolves the global
    `--auth-file` while the command action passes the subcommand's own. Before
    the fix the second call overwrote the cache silently and the first pooled
    client was orphaned, because the close-on-exit callback only ever sees the
    survivor.
    """
    _close_cached_application()
    _state["auth_file"] = None
    try:
        first = _application(tmp_path / "one.json")
        first_client = first.runner.lifecycle
        assert isinstance(first_client, HttpxEnjiHttpClient)
        assert first_client.is_closed is False

        second = _application(tmp_path / "two.json")

        assert second is not first
        assert first_client.is_closed is True
        second_client = second.runner.lifecycle
        assert isinstance(second_client, HttpxEnjiHttpClient)
        assert second_client.is_closed is False
    finally:
        _close_cached_application()
