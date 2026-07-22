import asyncio
from pathlib import Path

import pytest

import enji_guard_cli.runtime_observability.supervisor as runtime
from enji_guard_cli.delivery.mcp.server import McpTransport
from enji_guard_cli.runtime_observability.ports import BackendReadinessObservation
from enji_guard_cli.runtime_observability.readiness import (
    backend_readiness_starting_state,
    read_backend_readiness_state,
)
from enji_guard_cli.settings import ReadinessSettings


def test_run_service_async_supervises_mcp_and_refresh_as_sibling_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    refresh_started = False
    refresh_cancelled = False
    readiness_started = False
    readiness_cancelled = False
    served_while_refresh_was_running = False
    refresh_tasks: list[asyncio.Task[None]] = []
    readiness_tasks: list[asyncio.Task[None]] = []
    sentinel_settings = runtime.default_settings()

    async def fake_refresh_loop() -> None:
        nonlocal refresh_cancelled, refresh_started

        refresh_started = True
        try:
            await asyncio.Future[None]()
        finally:
            refresh_cancelled = True

    async def fake_readiness_loop() -> None:
        nonlocal readiness_cancelled, readiness_started

        readiness_started = True
        try:
            await asyncio.Future[None]()
        finally:
            readiness_cancelled = True

    def fake_start_auto_refresh_task() -> asyncio.Task[None]:
        refresh_task = asyncio.create_task(fake_refresh_loop())
        refresh_tasks.append(refresh_task)
        return refresh_task

    def fake_start_backend_readiness_task(*, observer: object, settings: object) -> asyncio.Task[None]:
        assert observer is auth
        assert settings is sentinel_settings
        readiness_task = asyncio.create_task(fake_readiness_loop())
        readiness_tasks.append(readiness_task)
        return readiness_task

    async def fake_run_mcp_server_async(
        server: object,
        *,
        transport: McpTransport = "stdio",
        mount_path: str | None = None,
    ) -> None:
        nonlocal served_while_refresh_was_running

        assert server == "server"
        assert transport == "streamable-http"
        assert mount_path is None
        await asyncio.sleep(0)
        served_while_refresh_was_running = (
            refresh_started
            and readiness_started
            and len(refresh_tasks) == 1
            and len(readiness_tasks) == 1
            and not refresh_tasks[0].done()
            and not readiness_tasks[0].done()
        )

    monkeypatch.setattr(runtime, "start_backend_readiness_task", fake_start_backend_readiness_task)

    class FakeRuntimeAuth:
        def start_auto_refresh_task(self) -> asyncio.Task[None]:
            return fake_start_auto_refresh_task()

        async def observe_backend_readiness(self) -> BackendReadinessObservation:
            return BackendReadinessObservation(ready=True)

        async def credential_changes(self):
            await asyncio.Event().wait()
            yield

    auth = FakeRuntimeAuth()

    asyncio.run(
        runtime.run_service_async(
            options=runtime.RuntimeServiceOptions(transport="streamable-http", host="0.0.0.0", port=8000),
            runtime_auth=auth,
            settings=sentinel_settings,
            mcp_server_factory=lambda host, port: "server",
            mcp_server_runner=fake_run_mcp_server_async,
        )
    )

    assert served_while_refresh_was_running is True
    assert refresh_cancelled is True
    assert readiness_cancelled is True
    assert len(refresh_tasks) == 1
    assert len(readiness_tasks) == 1
    assert refresh_tasks[0].cancelled()
    assert readiness_tasks[0].cancelled()


def test_run_service_async_runs_without_refresh_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_mcp_server_async(
        server: object,
        *,
        transport: McpTransport = "stdio",
        mount_path: str | None = None,
    ) -> None:
        captured["server"] = server
        captured["transport"] = transport
        captured["mount_path"] = mount_path

    monkeypatch.setattr(runtime, "start_backend_readiness_task", lambda *, observer, settings: None)

    asyncio.run(
        runtime.run_service_async(
            options=runtime.RuntimeServiceOptions(transport="sse", host="127.0.0.1", port=9000, mount_path="/events"),
            mcp_server_factory=lambda host, port: {"host": host, "port": port},
            mcp_server_runner=fake_run_mcp_server_async,
        )
    )

    assert captured == {
        "server": {"host": "127.0.0.1", "port": 9000},
        "transport": "sse",
        "mount_path": "/events",
    }


def test_backend_readiness_loop_records_probe_crash_without_propagating(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    waited = False

    class BrokenObserver:
        def start_auto_refresh_task(self) -> asyncio.Task[None] | None:
            return None

        async def observe_backend_readiness(self) -> BackendReadinessObservation:
            raise ValueError("broken probe")

        async def credential_changes(self):
            await asyncio.Future[None]()
            yield None

    async def fake_wait_for_readiness_trigger(change_task: asyncio.Task[None], interval_seconds: int) -> bool:
        nonlocal waited

        assert interval_seconds == 30
        assert not change_task.done()
        waited = True
        raise asyncio.CancelledError

    settings = ReadinessSettings(
        enabled=True,
        state_file=tmp_path / "readiness.json",
        heartbeat_interval_seconds=30,
        heartbeat_timeout_seconds=2.0,
        failure_threshold=3,
        state_stale_after_seconds=60,
    )

    def fake_log_event(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(runtime, "log_event", fake_log_event)
    monkeypatch.setattr(runtime, "_wait_for_readiness_trigger", fake_wait_for_readiness_trigger)

    initial_state = backend_readiness_starting_state(checked_at=runtime.datetime.now(runtime.UTC))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            runtime._backend_readiness_loop(settings=settings, initial_state=initial_state, observer=BrokenObserver())
        )

    state = read_backend_readiness_state(settings.state_file)
    assert waited is True
    assert state is not None
    assert state.ready is False
    assert state.failure_kind == "internal"
    assert state.failure_code == "ValueError"


def test_backend_readiness_loop_reprobes_immediately_after_credential_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probes = 0

    class Observer:
        async def observe_backend_readiness(self) -> BackendReadinessObservation:
            nonlocal probes

            probes += 1
            if probes == 2:
                raise asyncio.CancelledError
            return BackendReadinessObservation(ready=False, failure_code="AUTH_REQUIRED")

        async def credential_changes(self):
            yield None
            await asyncio.Future[None]()

    settings = ReadinessSettings(
        enabled=True,
        state_file=tmp_path / "readiness.json",
        heartbeat_interval_seconds=300,
        heartbeat_timeout_seconds=2.0,
        failure_threshold=3,
        state_stale_after_seconds=600,
    )
    monkeypatch.setattr(runtime, "log_event", lambda *_args, **_kwargs: None)
    initial_state = backend_readiness_starting_state(checked_at=runtime.datetime.now(runtime.UTC))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            runtime._backend_readiness_loop(settings=settings, initial_state=initial_state, observer=Observer())
        )

    assert probes == 2


def test_start_backend_readiness_task_writes_starting_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = ReadinessSettings(
        enabled=True,
        state_file=tmp_path / "readiness.json",
        heartbeat_interval_seconds=30,
        heartbeat_timeout_seconds=2.0,
        failure_threshold=3,
        state_stale_after_seconds=60,
    )
    captured_initial_state: list[runtime.BackendReadinessState] = []

    async def fake_backend_readiness_loop(
        *,
        settings: ReadinessSettings,
        initial_state: runtime.BackendReadinessState,
        observer: object,
    ) -> None:
        assert observer is not None
        captured_initial_state.append(initial_state)
        await asyncio.Future[None]()

    monkeypatch.setattr(runtime, "default_settings", lambda: type("Settings", (), {"readiness": settings})())
    monkeypatch.setattr(runtime, "_backend_readiness_loop", fake_backend_readiness_loop)

    class RuntimeAuth:
        def start_auto_refresh_task(self) -> asyncio.Task[None] | None:
            return None

        async def observe_backend_readiness(self) -> BackendReadinessObservation:
            return BackendReadinessObservation(ready=True)

        async def credential_changes(self):
            await asyncio.Event().wait()
            yield

    async def run_start() -> None:
        task = runtime.start_backend_readiness_task(observer=RuntimeAuth())
        assert task is not None
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_start())

    state = read_backend_readiness_state(settings.state_file)
    assert state is not None
    assert state.ready is False
    assert state.failure_kind == "startup"
    assert state.failure_code == "STARTING"
    assert state.last_success_at is None
    assert captured_initial_state == [state]
