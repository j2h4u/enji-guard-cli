import asyncio
from contextlib import suppress

from enji_guard_cli.auth import start_auto_refresh_task
from enji_guard_cli.mcp_server import McpTransport, create_mcp_server, run_mcp_server_async


async def run_service_async(
    *,
    transport: McpTransport,
    host: str,
    port: int,
    mount_path: str | None = None,
) -> None:
    mcp_task = asyncio.create_task(
        run_mcp_server_async(create_mcp_server(host=host, port=port), transport=transport, mount_path=mount_path),
        name="enji-guard-mcp-server",
    )
    auto_refresh_task = start_auto_refresh_task()
    await _supervise_tasks(mcp_task, auto_refresh_task)


def run_service(
    *,
    transport: McpTransport,
    host: str,
    port: int,
    mount_path: str | None = None,
) -> None:
    asyncio.run(run_service_async(transport=transport, host=host, port=port, mount_path=mount_path))


async def _supervise_tasks(
    mcp_task: asyncio.Task[None],
    auto_refresh_task: asyncio.Task[None] | None,
) -> None:
    supervised_tasks = {mcp_task}
    if auto_refresh_task is not None:
        supervised_tasks.add(auto_refresh_task)
    try:
        done, _pending = await asyncio.wait(supervised_tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        await _cancel_tasks(supervised_tasks)


async def _cancel_tasks(tasks: set[asyncio.Task[None]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
