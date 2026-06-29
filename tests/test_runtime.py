import asyncio

import pytest

import enji_guard_cli.runtime as runtime
from enji_guard_cli.mcp_server import McpTransport


def test_run_service_async_supervises_mcp_and_refresh_as_sibling_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    refresh_started = False
    refresh_cancelled = False
    served_while_refresh_was_running = False
    refresh_tasks: list[asyncio.Task[None]] = []

    async def fake_refresh_loop() -> None:
        nonlocal refresh_cancelled, refresh_started

        refresh_started = True
        try:
            await asyncio.Future[None]()
        finally:
            refresh_cancelled = True

    def fake_start_auto_refresh_task() -> asyncio.Task[None]:
        refresh_task = asyncio.create_task(fake_refresh_loop())
        refresh_tasks.append(refresh_task)
        return refresh_task

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
        served_while_refresh_was_running = refresh_started and len(refresh_tasks) == 1 and not refresh_tasks[0].done()

    monkeypatch.setattr(runtime, "create_mcp_server", lambda host, port: "server")
    monkeypatch.setattr(runtime, "start_auto_refresh_task", fake_start_auto_refresh_task)
    monkeypatch.setattr(runtime, "run_mcp_server_async", fake_run_mcp_server_async)

    asyncio.run(runtime.run_service_async(transport="streamable-http", host="0.0.0.0", port=8000))

    assert served_while_refresh_was_running is True
    assert refresh_cancelled is True
    assert len(refresh_tasks) == 1
    assert refresh_tasks[0].cancelled()


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

    monkeypatch.setattr(runtime, "create_mcp_server", lambda host, port: {"host": host, "port": port})
    monkeypatch.setattr(runtime, "start_auto_refresh_task", lambda: None)
    monkeypatch.setattr(runtime, "run_mcp_server_async", fake_run_mcp_server_async)

    asyncio.run(runtime.run_service_async(transport="sse", host="127.0.0.1", port=9000, mount_path="/events"))

    assert captured == {
        "server": {"host": "127.0.0.1", "port": 9000},
        "transport": "sse",
        "mount_path": "/events",
    }
