import asyncio

from enji_guard_cli.runtime_observability.supervisor import supervise_tasks


def test_supervisor_keeps_sibling_tasks_alive_until_shutdown_signal() -> None:
    cancelled: set[str] = set()

    async def sibling(name: str) -> None:
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.add(name)

    async def scenario() -> None:
        shutdown = asyncio.Event()
        mcp_task = asyncio.create_task(sibling("mcp"))
        refresh_task = asyncio.create_task(sibling("refresh"))
        readiness_task = asyncio.create_task(sibling("readiness"))
        supervisor_task = asyncio.create_task(
            supervise_tasks(mcp_task, refresh_task, readiness_task, shutdown_event=shutdown)
        )

        await asyncio.sleep(0)
        assert supervisor_task.done() is False
        assert cancelled == set()

        shutdown.set()
        await supervisor_task

        assert cancelled == {"mcp", "refresh", "readiness"}
        assert mcp_task.cancelled()
        assert refresh_task.cancelled()
        assert readiness_task.cancelled()

    asyncio.run(scenario())


def test_supervisor_awaits_mcp_natural_shutdown_before_cleanup() -> None:
    async def scenario() -> None:
        shutdown = asyncio.Event()

        async def mcp_server() -> None:
            await shutdown.wait()

        async def sibling() -> None:
            await asyncio.Future[None]()

        mcp_task = asyncio.create_task(mcp_server())
        refresh_task = asyncio.create_task(sibling())
        readiness_task = asyncio.create_task(sibling())
        supervisor_task = asyncio.create_task(
            supervise_tasks(mcp_task, refresh_task, readiness_task, shutdown_event=shutdown)
        )

        await asyncio.sleep(0)
        shutdown.set()
        await supervisor_task

        assert mcp_task.done()
        assert mcp_task.cancelled() is False
        assert refresh_task.cancelled()
        assert readiness_task.cancelled()

    asyncio.run(scenario())
