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
from enji_guard_cli.runtime_observability.auth_coordinator import RuntimeAuthCoordinatorAdapter
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

    def __init__(self, *, limits: object, retry_config: object, event_sink: object) -> None:
        self.limits = limits
        self.retry_config = retry_config
        self.event_sink = event_sink
        self.owner_thread = threading.get_ident()
        self.owner_loop = asyncio.get_running_loop()
        self.request_threads: set[int] = set()
        self.request_loops: set[asyncio.AbstractEventLoop] = set()
        self.closed = False
        self.close_calls = 0
        self.entered = threading.Event()
        self.release: asyncio.Event | None = None
        self.before_release: Callable[[], None] | None = None
        type(self).instances.append(self)

    async def request(self, _request: EnjiHttpRequest) -> EnjiHttpResponse:
        self.request_threads.add(threading.get_ident())
        self.request_loops.add(asyncio.get_running_loop())
        self.entered.set()
        if self.before_release is not None:
            self.before_release()
        if self.release is not None:
            await self.release.wait()
        else:
            await asyncio.sleep(0)
        return RESPONSE

    async def __aexit__(self, *_: object) -> None:
        self.close_calls += 1
        self.closed = True


class _FailingExecutor(_RecordingExecutor):
    def __init__(self, *, limits: object, retry_config: object, event_sink: object) -> None:
        del limits, retry_config, event_sink
        raise RuntimeError("owner loop failed")


class _FailingCloseExecutor(_RecordingExecutor):
    def __init__(self, *, limits: object, retry_config: object, event_sink: object) -> None:
        super().__init__(limits=limits, retry_config=retry_config, event_sink=event_sink)
        self.close_entered = threading.Event()
        self.close_release = asyncio.Event()

    async def __aexit__(self, *_: object) -> None:
        self.close_calls += 1
        self.close_entered.set()
        await self.close_release.wait()
        raise RuntimeError("executor close failed")


class _ShutdownWaitObserver:
    def __init__(self, event: threading.Event, wait_entered: threading.Event) -> None:
        self._event = event
        self._wait_entered = wait_entered

    def set(self) -> None:
        self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        self._wait_entered.set()
        return self._event.wait(timeout)


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

    close_waiting = threading.Event()
    original_wait = cast(Callable[..., object], pooled_client_module.wait)

    def instrumented_wait(*args: object, **kwargs: object) -> object:
        close_waiting.set()
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(pooled_client_module, "wait", instrumented_wait)
    closer = threading.Thread(target=client.close)
    closer.start()
    assert close_waiting.wait(timeout=2)
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


def test_pooled_client_concurrent_close_waits_for_complete_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingExecutor.instances.clear()
    monkeypatch.setattr(pooled_client_module, "HttpxEnjiHttpClient", _RecordingExecutor)
    client = pooled_client_module.PooledEnjiHttpClient(default_settings())
    executor = _RecordingExecutor.instances[0]
    executor.release = asyncio.Event()
    request_worker = threading.Thread(target=_run_request, args=(client,))
    request_worker.start()
    assert executor.entered.wait(timeout=2)

    close_waiting = threading.Event()
    original_wait = cast(Callable[..., object], pooled_client_module.wait)

    def instrumented_wait(*args: object, **kwargs: object) -> object:
        close_waiting.set()
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(pooled_client_module, "wait", instrumented_wait)
    first_closer = threading.Thread(target=client.close)
    first_closer.start()
    assert close_waiting.wait(timeout=2)
    with pytest.raises(RuntimeError, match="closed"):
        asyncio.run(client.request(REQUEST))

    second_close_waiting = threading.Event()
    client._shutdown_complete = cast(
        threading.Event,
        _ShutdownWaitObserver(client._shutdown_complete, second_close_waiting),
    )
    second_close_returned = threading.Event()
    owner_alive_when_second_close_returns: list[bool] = []

    def second_close() -> None:
        client.close()
        owner_alive_when_second_close_returns.append(client._thread.is_alive())
        second_close_returned.set()

    second_closer = threading.Thread(target=second_close)
    second_closer.start()
    assert second_close_waiting.wait(timeout=2)
    assert not second_close_returned.wait(timeout=0.1), "second close returned before shutdown completed"
    assert second_closer.is_alive()

    executor.owner_loop.call_soon_threadsafe(executor.release.set)
    request_worker.join(timeout=2)
    first_closer.join(timeout=2)
    second_closer.join(timeout=2)
    assert not request_worker.is_alive()
    assert not first_closer.is_alive()
    assert not second_closer.is_alive()
    assert second_close_returned.is_set()
    assert owner_alive_when_second_close_returns == [False]
    assert executor.closed
    assert executor.close_calls == 1
    assert not client._thread.is_alive()


def test_pooled_client_close_failure_stops_owner_and_is_replayed_to_waiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingExecutor.instances.clear()
    monkeypatch.setattr(pooled_client_module, "HttpxEnjiHttpClient", _FailingCloseExecutor)
    client = pooled_client_module.PooledEnjiHttpClient(default_settings())
    executor = cast(_FailingCloseExecutor, _RecordingExecutor.instances[0])
    second_close_waiting = threading.Event()
    client._shutdown_complete = cast(
        threading.Event,
        _ShutdownWaitObserver(client._shutdown_complete, second_close_waiting),
    )
    errors: list[BaseException] = []

    def close_capturing_error() -> None:
        try:
            client.close()
        except BaseException as exc:  # noqa: BLE001 - capture the terminal close result from both callers
            errors.append(exc)

    first_closer = threading.Thread(target=close_capturing_error)
    first_closer.start()
    assert executor.close_entered.wait(timeout=2)
    second_closer = threading.Thread(target=close_capturing_error)
    second_closer.start()
    assert second_close_waiting.wait(timeout=2)

    executor.owner_loop.call_soon_threadsafe(executor.close_release.set)
    first_closer.join(timeout=2)
    second_closer.join(timeout=2)
    assert not first_closer.is_alive()
    assert not second_closer.is_alive()
    assert len(errors) == 2
    assert all(isinstance(error, RuntimeError) for error in errors)
    assert all(str(error) == "executor close failed" for error in errors)
    assert executor.close_calls == 1
    assert not client._thread.is_alive()

    with pytest.raises(RuntimeError, match="executor close failed"):
        client.close()
    assert executor.close_calls == 1


def test_owner_thread_close_defers_shutdown_until_admitted_request_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingExecutor.instances.clear()
    monkeypatch.setattr(pooled_client_module, "HttpxEnjiHttpClient", _RecordingExecutor)
    client = pooled_client_module.PooledEnjiHttpClient(default_settings())
    executor = _RecordingExecutor.instances[0]
    executor.release = asyncio.Event()
    request_worker = threading.Thread(target=_run_request, args=(client,))
    request_worker.start()
    assert executor.entered.wait(timeout=2)

    external_close_waiting = threading.Event()
    client._shutdown_complete = cast(
        threading.Event,
        _ShutdownWaitObserver(client._shutdown_complete, external_close_waiting),
    )
    owner_close_returned = threading.Event()

    def close_from_owner_thread() -> None:
        client.close()
        owner_close_returned.set()

    executor.owner_loop.call_soon_threadsafe(close_from_owner_thread)
    assert owner_close_returned.wait(timeout=2), "owner-thread close must not block its loop"
    assert request_worker.is_alive()
    assert executor.close_calls == 0
    with pytest.raises(RuntimeError, match="closed"):
        asyncio.run(client.request(REQUEST))

    external_close_returned = threading.Event()
    owner_alive_when_external_close_returns: list[bool] = []

    def close_from_external_thread() -> None:
        client.close()
        owner_alive_when_external_close_returns.append(client._thread.is_alive())
        external_close_returned.set()

    external_closer = threading.Thread(target=close_from_external_thread)
    external_closer.start()
    assert external_close_waiting.wait(timeout=2)
    assert not external_close_returned.wait(timeout=0.1)

    executor.owner_loop.call_soon_threadsafe(executor.release.set)
    request_worker.join(timeout=2)
    external_closer.join(timeout=2)
    assert not request_worker.is_alive()
    assert not external_closer.is_alive()
    assert external_close_returned.is_set()
    assert owner_alive_when_external_close_returns == [False]
    assert executor.close_calls == 1
    assert executor.closed
    assert not client._thread.is_alive()

    client.close()
    assert executor.close_calls == 1


def test_owner_loop_request_is_drained_before_owner_thread_close_stops_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingExecutor.instances.clear()
    monkeypatch.setattr(pooled_client_module, "HttpxEnjiHttpClient", _RecordingExecutor)
    client = pooled_client_module.PooledEnjiHttpClient(default_settings())
    executor = _RecordingExecutor.instances[0]
    executor.release = asyncio.Event()
    owner_request_finished = threading.Event()
    owner_close_returned = threading.Event()
    owner_request_result: list[EnjiHttpResponse] = []

    async def request_from_owner_loop() -> None:
        owner_request_result.append(await client.request(REQUEST))
        owner_request_finished.set()

    def start_owner_request() -> None:
        executor.owner_loop.create_task(request_from_owner_loop())

    executor.owner_loop.call_soon_threadsafe(start_owner_request)
    assert executor.entered.wait(timeout=2)

    external_close_waiting = threading.Event()
    client._shutdown_complete = cast(
        threading.Event,
        _ShutdownWaitObserver(client._shutdown_complete, external_close_waiting),
    )

    async def close_from_separate_owner_task() -> None:
        client.close()
        owner_close_returned.set()

    def start_owner_close() -> None:
        executor.owner_loop.create_task(close_from_separate_owner_task())

    executor.owner_loop.call_soon_threadsafe(start_owner_close)
    assert owner_close_returned.wait(timeout=2), "owner-thread close must not block its loop"
    assert not owner_request_finished.is_set()
    assert executor.close_calls == 0

    external_close_returned = threading.Event()

    def close_from_external_thread() -> None:
        client.close()
        external_close_returned.set()

    external_closer = threading.Thread(target=close_from_external_thread)
    external_closer.start()
    assert external_close_waiting.wait(timeout=2)
    assert not external_close_returned.wait(timeout=0.1)

    executor.owner_loop.call_soon_threadsafe(executor.release.set)
    assert owner_request_finished.wait(timeout=2)
    external_closer.join(timeout=2)
    assert not external_closer.is_alive()
    assert external_close_returned.is_set()
    assert owner_request_result == [RESPONSE]
    assert executor.close_calls == 1
    assert executor.closed
    assert not client._thread.is_alive()


def test_owner_request_self_close_defers_shutdown_until_that_request_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingExecutor.instances.clear()
    monkeypatch.setattr(pooled_client_module, "HttpxEnjiHttpClient", _RecordingExecutor)
    client = pooled_client_module.PooledEnjiHttpClient(default_settings())
    executor = _RecordingExecutor.instances[0]
    executor.release = asyncio.Event()
    self_close_returned = threading.Event()
    owner_request_finished = threading.Event()
    owner_request_result: list[EnjiHttpResponse] = []

    def close_from_admitted_request() -> None:
        client.close()
        self_close_returned.set()

    executor.before_release = close_from_admitted_request

    async def self_closing_owner_request() -> None:
        owner_request_result.append(await client.request(REQUEST))
        owner_request_finished.set()

    executor.owner_loop.call_soon_threadsafe(lambda: executor.owner_loop.create_task(self_closing_owner_request()))
    assert executor.entered.wait(timeout=2)
    assert self_close_returned.wait(timeout=2), "self-close must return before the request can resume"
    assert not owner_request_finished.is_set()
    assert executor.close_calls == 0

    external_close_waiting = threading.Event()
    client._shutdown_complete = cast(
        threading.Event,
        _ShutdownWaitObserver(client._shutdown_complete, external_close_waiting),
    )
    external_close_returned = threading.Event()

    def close_from_external_thread() -> None:
        client.close()
        external_close_returned.set()

    external_closer = threading.Thread(target=close_from_external_thread)
    external_closer.start()
    assert external_close_waiting.wait(timeout=2)
    assert not external_close_returned.wait(timeout=0.1)

    executor.owner_loop.call_soon_threadsafe(executor.release.set)
    assert owner_request_finished.wait(timeout=2)
    external_closer.join(timeout=2)
    assert not external_closer.is_alive()
    assert external_close_returned.is_set()
    assert owner_request_result == [RESPONSE]
    assert executor.close_calls == 1
    assert executor.closed
    assert not client._thread.is_alive()


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

    monkeypatch.setattr(composition_module, "PooledEnjiHttpClient", make_client)
    monkeypatch.setattr(composition_module, "AuditGateway", Gateway)
    monkeypatch.setattr(composition_module, "PortfolioGateway", Gateway)
    monkeypatch.setattr(composition_module, "FileAuditLedger", lambda *_args, **_kwargs: object())

    application = create_application(tmp_path / "auth.json")
    assert len(client_instances) == 1
    assert cast(Gateway, application.audit_gateway).client is client_instances[0]
    assert cast(Gateway, application.portfolio_gateway).client is client_instances[0]
    assert client_instances[0].event_sink is composition_module.log_event
    assert application.auth.client is client_instances[0]
    assert isinstance(application.runtime_auth, RuntimeAuthCoordinatorAdapter)
    assert application.runtime_auth.client is client_instances[0]
    assert application.lifecycle is client_instances[0]


def test_composition_closes_pool_when_gateway_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import enji_guard_cli.composition as composition_module

    class Client:
        close_calls = 0

        def __init__(self, _settings: object, *, event_sink: object) -> None:
            del event_sink

        def close(self) -> None:
            self.close_calls += 1

    client = Client(object(), event_sink=object())
    monkeypatch.setattr(composition_module, "PooledEnjiHttpClient", lambda _settings, *, event_sink: client)
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
