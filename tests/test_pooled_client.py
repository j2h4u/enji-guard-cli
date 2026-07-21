from __future__ import annotations

import asyncio
import importlib
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import ClassVar, Protocol, cast

import pytest
import typer
from typer.testing import CliRunner

from enji_guard_cli.application import Application
from enji_guard_cli.audit.ports import AuditGatewayPort
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.composition import create_application
from enji_guard_cli.enji_gateway import pooled_client as pooled_client_module
from enji_guard_cli.portfolio.models import AccessInfo, AccessLimits
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort
from enji_guard_cli.runtime_observability import supervisor as supervisor_module
from enji_guard_cli.settings import default_settings
from enji_guard_cli.transport import EnjiHttpRequest, EnjiHttpResponse


class _CliModule(Protocol):
    app: typer.Typer
    _state: dict[str, object]
    create_application: Callable[[Path | None], Application]


cli_module = cast(
    _CliModule,
    importlib.import_module("enji_guard_cli.delivery.cli.app"),
)


REQUEST = EnjiHttpRequest(
    method="GET",
    url="https://fleet.enji.ai/api/test",
    operation="test",
    headers={},
)
RESPONSE = EnjiHttpResponse(status_code=200, headers={}, content=b"{}")


class _RecordingExecutor:
    instances: ClassVar[list[_RecordingExecutor]] = []

    def __init__(self, *, limits: object, retry_config: object) -> None:
        self.limits = limits
        self.retry_config = retry_config
        self.owner_thread = threading.get_ident()
        self.owner_loop = asyncio.get_running_loop()
        self.request_threads: set[int] = set()
        self.request_loops: set[asyncio.AbstractEventLoop] = set()
        self.closed = False
        self.entered = threading.Event()
        self.release: asyncio.Event | None = None
        type(self).instances.append(self)

    async def request(self, _request: EnjiHttpRequest) -> EnjiHttpResponse:
        self.request_threads.add(threading.get_ident())
        self.request_loops.add(asyncio.get_running_loop())
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        else:
            await asyncio.sleep(0)
        return RESPONSE

    async def __aexit__(self, *_: object) -> None:
        self.closed = True


class _FailingExecutor(_RecordingExecutor):
    def __init__(self, *, limits: object, retry_config: object) -> None:
        raise RuntimeError("owner loop failed")


def _run_request(client: pooled_client_module.PooledEnjiHttpClient) -> EnjiHttpResponse:
    return asyncio.run(client.request(REQUEST))


def test_pooled_client_reuses_one_owner_executor_across_asyncio_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingExecutor.instances.clear()
    monkeypatch.setattr(pooled_client_module, "HttpxEnjiHttpClient", _RecordingExecutor)
    client = pooled_client_module.PooledEnjiHttpClient(default_settings())
    try:
        with ThreadPoolExecutor(max_workers=8) as workers:
            for _ in range(2):
                results = list(workers.map(lambda _: _run_request(client), range(16)))
                assert all(result.status_code == 200 for result in results)

        assert len(_RecordingExecutor.instances) == 1
        executor = _RecordingExecutor.instances[0]
        assert executor.request_threads == {executor.owner_thread}
        assert executor.request_loops == {executor.owner_loop}
    finally:
        client.close()


def test_pooled_client_close_waits_for_inflight_request_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingExecutor.instances.clear()
    monkeypatch.setattr(pooled_client_module, "HttpxEnjiHttpClient", _RecordingExecutor)
    client = pooled_client_module.PooledEnjiHttpClient(default_settings())
    executor = _RecordingExecutor.instances[0]
    executor.release = asyncio.Event()
    worker = threading.Thread(target=_run_request, args=(client,))
    worker.start()
    assert executor.entered.wait(timeout=2)

    closer = threading.Thread(target=client.close)
    closer.start()
    time.sleep(0.05)
    assert closer.is_alive(), "close must wait for an in-flight owner-loop request"

    executor.owner_loop.call_soon_threadsafe(executor.release.set)
    worker.join(timeout=2)
    closer.join(timeout=2)
    assert not worker.is_alive()
    assert not closer.is_alive()
    assert executor.closed

    close_calls = executor.closed
    client.close()
    assert executor.closed is close_calls
    with pytest.raises(RuntimeError, match="closed"):
        asyncio.run(client.request(REQUEST))


def test_pooled_client_caller_cancellation_cancels_owner_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingExecutor.instances.clear()
    monkeypatch.setattr(pooled_client_module, "HttpxEnjiHttpClient", _RecordingExecutor)
    client = pooled_client_module.PooledEnjiHttpClient(default_settings())
    executor = _RecordingExecutor.instances[0]
    gate_ready = threading.Event()

    def install_gate() -> None:
        executor.release = asyncio.Event()
        gate_ready.set()

    executor.owner_loop.call_soon_threadsafe(install_gate)
    assert gate_ready.wait(timeout=2)

    async def scenario() -> None:
        request_task = asyncio.create_task(client.request(REQUEST))
        assert await asyncio.to_thread(executor.entered.wait, 2)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task

    try:
        asyncio.run(scenario())
    finally:
        client.close()
    assert executor.closed


def test_pooled_client_startup_failure_is_reported_without_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pooled_client_module, "HttpxEnjiHttpClient", _FailingExecutor)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="failed to start"):
        pooled_client_module.PooledEnjiHttpClient(default_settings())
    assert time.monotonic() - started < 2


def test_settings_expose_pool_and_graceful_shutdown_values() -> None:
    settings = default_settings()
    assert settings.transport.pool.max_connections == 20
    assert settings.transport.pool.max_keepalive_connections == 20
    assert settings.transport.pool.keepalive_expiry_seconds == 5.0
    assert settings.service.mcp_graceful_shutdown_timeout_seconds == 5.0


def test_auto_refresh_retry_wait_uses_injected_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from enji_guard_cli.auth_session import auto_refresh as auto_refresh_module
    from enji_guard_cli.settings import AutoRefreshSettings

    monkeypatch.setattr(auto_refresh_module.random, "uniform", lambda _low, _high: 0.0)
    settings = AutoRefreshSettings(
        enabled=True,
        lead_seconds=300,
        fallback_seconds=900,
        retry_seconds=900,
        retry_initial_seconds=2.0,
        retry_max_seconds=7.5,
        retry_jitter_seconds=30.0,
        auth_required_retry_seconds=900,
    )
    wait = auto_refresh_module._AuthRefreshWait(settings)
    state = SimpleNamespace(
        attempt_number=20,
        outcome=SimpleNamespace(exception=lambda: RuntimeError()),
    )
    assert wait(cast(auto_refresh_module.RetryCallState, state)) == 7.5


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

    class Client:
        def __init__(self, _settings: object) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    class Gateway:
        def __init__(self, _auth_file: Path | None, client: object, *, auth_port: object) -> None:
            del auth_port
            self.client = client

    client_instances: list[Client] = []

    def make_client(settings: object) -> Client:
        client = Client(settings)
        client_instances.append(client)
        return client

    monkeypatch.setattr(composition_module, "PooledEnjiHttpClient", make_client)
    monkeypatch.setattr(composition_module, "AuditGateway", Gateway)
    monkeypatch.setattr(composition_module, "PortfolioGateway", Gateway)
    monkeypatch.setattr(composition_module, "FileAuditLedger", lambda *_args, **_kwargs: object())

    application = create_application(tmp_path / "auth.json")
    assert len(client_instances) == 1
    assert cast(Gateway, application.audit_gateway).client is client_instances[0]
    assert cast(Gateway, application.portfolio_gateway).client is client_instances[0]
    assert application.lifecycle is client_instances[0]


def test_composition_closes_pool_when_gateway_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import enji_guard_cli.composition as composition_module

    class Client:
        close_calls = 0

        def __init__(self, _settings: object) -> None:
            pass

        def close(self) -> None:
            self.close_calls += 1

    client = Client(object())
    monkeypatch.setattr(composition_module, "PooledEnjiHttpClient", lambda _settings: client)
    monkeypatch.setattr(composition_module, "PortfolioGateway", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        composition_module,
        "AuditGateway",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("gateway construction failed")),
    )
    monkeypatch.setattr(composition_module, "FileAuditLedger", lambda *_args, **_kwargs: object())

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
    application = Application(
        cast(AuditGatewayPort, object()),
        cast(PortfolioGatewayPort, _AccessGateway(failure=failure)),
        cast(AuthSessionService, object()),
        lifecycle=lifecycle,
    )
    monkeypatch.setattr(cli_module, "create_application", lambda _auth_file: application)
    cli_module._state["application"] = None

    result = CliRunner().invoke(cli_module.app, ["access", "--json"])
    assert result.exit_code == (1 if failure else 0)
    assert lifecycle.close_calls == 1
    assert cli_module._state["application"] is None
