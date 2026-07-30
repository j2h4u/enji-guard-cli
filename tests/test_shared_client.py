from __future__ import annotations

import asyncio
import collections
import http.server
import importlib
import socketserver
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol, cast

import httpx
import pytest
import typer
from typer.testing import CliRunner

from application_builder import ApplicationStubs
from enji_guard_cli.application import Application
from enji_guard_cli.composition import create_application
from enji_guard_cli.enji_gateway.shared_client import create_shared_http_client
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.portfolio.models import AccessInfo, AccessLimits
from enji_guard_cli.runtime_observability import supervisor as supervisor_module
from enji_guard_cli.settings import FanoutSettings, default_settings
from enji_guard_cli.transport import EnjiHttpRequest


class _CliModule(Protocol):
    app: typer.Typer
    _state: dict[str, object]
    create_application: Callable[[Path | None], Application]


cli_module = cast(
    _CliModule,
    importlib.import_module("enji_guard_cli.delivery.cli.app"),
)


class _ConnectionCountingServer:
    """Local HTTP/1.1 server that reports how many TCP connections it served."""

    def __init__(self) -> None:
        self.connections: collections.Counter[int] = collections.Counter()
        counter = self.connections

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                counter[id(cast(object, self.connection))] += 1
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Set-Cookie", "session=abc; Path=/")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/probe"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


@pytest.fixture
def local_server() -> Iterator[_ConnectionCountingServer]:
    server = _ConnectionCountingServer()
    try:
        yield server
    finally:
        server.close()


def _probe(server: _ConnectionCountingServer) -> EnjiHttpRequest:
    return EnjiHttpRequest(method="GET", url=server.url, operation="probe", headers={})


def test_shared_client_reuses_pooled_connections_across_sequential_requests(
    local_server: _ConnectionCountingServer,
) -> None:
    client = create_shared_http_client()
    try:
        for _ in range(10):
            response = client.request_blocking(_probe(local_server))
            assert response.status_code == 200
    finally:
        client.close()

    assert sum(local_server.connections.values()) == 10
    assert len(local_server.connections) == 1


def test_shared_client_is_thread_safe_and_pools_under_bounded_fanout(
    local_server: _ConnectionCountingServer,
) -> None:
    client = create_shared_http_client()
    fanout = BoundedFanout(FanoutSettings(max_concurrency=8))
    try:
        results = fanout.map(range(64), lambda _index: client.request_blocking(_probe(local_server)))
    finally:
        client.close()

    assert len(results) == 64
    assert all(item.status_code == 200 for item in results)
    assert all(item.set_cookie_headers == ("session=abc; Path=/",) for item in results)
    assert sum(local_server.connections.values()) == 64
    # Real pooling: far fewer TCP connections than requests, and never more than
    # the number of concurrent workers.
    assert len(local_server.connections) <= 8


def test_shared_client_async_facade_does_not_block_the_calling_loop(
    local_server: _ConnectionCountingServer,
) -> None:
    client = create_shared_http_client()

    async def scenario() -> tuple[int, ...]:
        try:
            responses = await asyncio.gather(*(client.request(_probe(local_server)) for _ in range(8)))
        finally:
            await asyncio.to_thread(client.close)
        return tuple(item.status_code for item in responses)

    assert asyncio.run(scenario()) == (200,) * 8


def test_shared_client_applies_configured_pool_limits() -> None:
    settings = default_settings()
    client = create_shared_http_client(settings)
    try:
        pool = cast(httpx.HTTPTransport, client._client._transport)._pool  # pyright: ignore[reportPrivateUsage]
        assert pool._max_connections == settings.transport.pool.max_connections
        assert pool._max_keepalive_connections == settings.transport.pool.max_keepalive_connections
        assert pool._keepalive_expiry == settings.transport.pool.keepalive_expiry_seconds
    finally:
        client.close()


def test_settings_expose_pool_and_graceful_shutdown_values() -> None:
    settings = default_settings()
    assert settings.transport.pool.max_connections == 20
    assert settings.transport.pool.max_keepalive_connections == 20
    assert settings.transport.pool.keepalive_expiry_seconds == 5.0
    assert settings.service.mcp_graceful_shutdown_timeout_seconds == 5.0


def test_supervisor_uses_configured_graceful_shutdown_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[float] = []

    async def fake_await_mcp_shutdown(task: asyncio.Task[None], *, timeout_seconds: float) -> None:
        captured.append(timeout_seconds)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    monkeypatch.setattr(supervisor_module, "_await_mcp_shutdown", fake_await_mcp_shutdown)

    async def scenario() -> None:
        shutdown = asyncio.Event()

        async def mcp_server() -> None:
            await asyncio.Future[None]()

        mcp_task = asyncio.create_task(mcp_server())
        shutdown_task = asyncio.create_task(
            supervisor_module.supervise_tasks(
                mcp_task,
                None,
                None,
                shutdown_event=shutdown,
                shutdown_timeout_seconds=0.125,
            )
        )
        await asyncio.sleep(0)
        shutdown.set()
        await shutdown_task

    asyncio.run(scenario())
    assert captured == [0.125]


def test_composition_injects_one_client_into_both_gateways_and_application_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import enji_guard_cli.composition as composition_module
    import enji_guard_cli.composition_support as composition_support_module

    class Client:
        def __init__(self, _settings: object, *, event_sink: object) -> None:
            self.close_calls = 0
            self.event_sink = event_sink

        def close(self) -> None:
            self.close_calls += 1

    class Gateway:
        def __init__(self, _auth_file: Path | None, client: object, *, auth_port: object) -> None:
            del auth_port
            self.client = client

    client_instances: list[Client] = []

    def make_client(settings: object, *, event_sink: object) -> Client:
        client = Client(settings, event_sink=event_sink)
        client_instances.append(client)
        return client

    monkeypatch.setattr(composition_module, "create_shared_http_client", make_client)
    monkeypatch.setattr(composition_support_module, "AuditGateway", Gateway)
    monkeypatch.setattr(composition_support_module, "PortfolioGateway", Gateway)
    monkeypatch.setattr(composition_support_module, "FileAuditLedger", lambda *_args, **_kwargs: object())

    application = create_application(tmp_path / "auth.json")
    assert len(client_instances) == 1
    assert cast(Gateway, application.audit.gateway).client is client_instances[0]
    assert cast(Gateway, application.portfolio.gateway).client is client_instances[0]
    assert client_instances[0].event_sink is composition_module.log_event
    assert application.auth.session.client is client_instances[0]
    assert application.runner.lifecycle is client_instances[0]


def test_composition_closes_pool_when_gateway_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import enji_guard_cli.composition as composition_module
    import enji_guard_cli.composition_support as composition_support_module

    class Client:
        close_calls = 0

        def __init__(self, _settings: object, *, event_sink: object) -> None:
            del event_sink

        def close(self) -> None:
            self.close_calls += 1

    client = Client(object(), event_sink=object())
    monkeypatch.setattr(composition_module, "create_shared_http_client", lambda _settings, *, event_sink: client)
    monkeypatch.setattr(composition_support_module, "PortfolioGateway", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        composition_support_module,
        "AuditGateway",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("gateway construction failed")),
    )
    monkeypatch.setattr(composition_support_module, "FileAuditLedger", lambda *_args, **_kwargs: object())

    with pytest.raises(RuntimeError, match="gateway construction failed"):
        create_application(tmp_path / "auth.json")

    assert client.close_calls == 1


class _Lifecycle:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _AccessGateway:
    def __init__(self, *, failure: bool = False) -> None:
        self.failure = failure

    def access(self) -> AccessInfo:
        if self.failure:
            raise RuntimeError("access failed")
        return AccessInfo("pro", True, AccessLimits(can_use_schedules=True))


@pytest.mark.parametrize("failure", [False, True])
def test_cli_callback_closes_cached_application_on_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
) -> None:
    lifecycle = _Lifecycle()
    application = ApplicationStubs(portfolio_gateway=_AccessGateway(failure=failure), lifecycle=lifecycle).build()
    monkeypatch.setattr(cli_module, "create_application", lambda _auth_file: application)
    cli_module._state["application"] = None

    result = CliRunner().invoke(cli_module.app, ["access", "--json"])
    assert result.exit_code == (1 if failure else 0)
    assert lifecycle.close_calls == 1
    assert cli_module._state["application"] is None
