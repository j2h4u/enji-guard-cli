"""Runtime supervisor owning sibling MCP, refresh, and readiness tasks."""

import asyncio
import logging
import signal
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.delivery.mcp.server import McpTransport, create_mcp_server, run_mcp_server_async
from enji_guard_cli.runtime_observability.readiness import (
    BackendReadinessProbe,
    BackendReadinessState,
    backend_readiness_starting_state,
    backend_readiness_state_after_probe,
    write_backend_readiness_state,
)
from enji_guard_cli.runtime_observability.telemetry import configure_logging, log_event
from enji_guard_cli.settings import ReadinessSettings, default_settings

_LOGGER = logging.getLogger(__name__)
MCP_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class RuntimeSupervisor:
    """Injectable supervisor facade used by service composition and tests."""

    auth_service: AuthSessionService | None = None

    async def run_async(
        self,
        *,
        transport: McpTransport,
        host: str,
        port: int,
        mount_path: str | None = None,
    ) -> None:
        await run_service_async(
            transport=transport,
            host=host,
            port=port,
            mount_path=mount_path,
            auth_service=self.auth_service,
        )


async def run_service_async(
    *,
    transport: McpTransport,
    host: str,
    port: int,
    mount_path: str | None = None,
    auth_service: AuthSessionService | None = None,
) -> None:
    """Start all sibling tasks and cancel them together on service exit."""
    mcp_task = asyncio.create_task(
        run_mcp_server_async(create_mcp_server(host=host, port=port), transport=transport, mount_path=mount_path),
        name="enji-guard-mcp-server",
    )
    service = auth_service or AuthSessionService()
    refresh_task = service.start_auto_refresh_task()
    readiness_task = start_backend_readiness_task(auth_service=service)
    shutdown_event = asyncio.Event()
    installed_signals = _install_signal_handlers(shutdown_event)
    try:
        await supervise_tasks(mcp_task, refresh_task, readiness_task, shutdown_event=shutdown_event)
    finally:
        _remove_signal_handlers(installed_signals)


def run_service(*, transport: McpTransport, host: str, port: int, mount_path: str | None = None) -> None:
    configure_logging(default_settings().telemetry, provenance="supervisor")
    asyncio.run(run_service_async(transport=transport, host=host, port=port, mount_path=mount_path))


async def supervise_tasks(
    mcp_task: asyncio.Task[None],
    refresh_task: asyncio.Task[None] | None,
    readiness_task: asyncio.Task[None] | None,
    *,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    tasks = {mcp_task}
    if refresh_task is not None:
        tasks.add(refresh_task)
    if readiness_task is not None:
        tasks.add(readiness_task)
    shutdown_task: asyncio.Task[None] | None = None
    if shutdown_event is not None:
        shutdown_task = asyncio.create_task(_wait_for_shutdown(shutdown_event), name="enji-guard-shutdown-waiter")
        tasks.add(shutdown_task)
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        graceful_shutdown_requested = shutdown_task in done
        for task in done:
            if task is shutdown_task:
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "enji_guard_shutdown_requested",
                    {"reason": "signal"},
                )
                continue
            if graceful_shutdown_requested and task is mcp_task:
                continue
            task.result()
        if graceful_shutdown_requested:
            await _stop_sibling_tasks(tasks, shutdown_task=shutdown_task, mcp_task=mcp_task)
            await _await_mcp_shutdown(mcp_task)
    finally:
        await _cancel_tasks(tasks)


async def _stop_sibling_tasks(
    tasks: set[asyncio.Task[None]],
    *,
    shutdown_task: asyncio.Task[None] | None,
    mcp_task: asyncio.Task[None],
) -> None:
    siblings = {task for task in tasks if task is not mcp_task and task is not shutdown_task}
    await _cancel_tasks(siblings)


async def _await_mcp_shutdown(mcp_task: asyncio.Task[None]) -> None:
    if mcp_task.done():
        mcp_task.result()
        return
    try:
        await asyncio.wait_for(asyncio.shield(mcp_task), timeout=MCP_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS)
    except TimeoutError:
        log_event(
            _LOGGER,
            logging.WARNING,
            "enji_guard_mcp_graceful_shutdown_timed_out",
            {"timeout_seconds": MCP_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS},
        )
        await _cancel_tasks({mcp_task})


async def _wait_for_shutdown(shutdown_event: asyncio.Event) -> None:
    await shutdown_event.wait()


def _install_signal_handlers(shutdown_event: asyncio.Event) -> tuple[signal.Signals, ...]:
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, shutdown_event.set)
        except NotImplementedError, RuntimeError:
            continue
        installed.append(signum)
    return tuple(installed)


def _remove_signal_handlers(signals: tuple[signal.Signals, ...]) -> None:
    loop = asyncio.get_running_loop()
    for signum in signals:
        with suppress(NotImplementedError, RuntimeError):
            loop.remove_signal_handler(signum)


def start_backend_readiness_task(*, auth_service: AuthSessionService | None = None) -> asyncio.Task[None] | None:
    settings = default_settings().readiness
    if not settings.enabled:
        return None
    initial_state = backend_readiness_starting_state(checked_at=datetime.now(UTC))
    _write_state(settings, initial_state)
    service = auth_service or AuthSessionService()
    return asyncio.create_task(
        _backend_readiness_loop(settings=settings, initial_state=initial_state, auth_service=service),
        name="enji-guard-backend-readiness",
    )


async def _backend_readiness_loop(
    *,
    settings: ReadinessSettings,
    initial_state: BackendReadinessState,
    auth_service: AuthSessionService | None = None,
) -> None:
    state = initial_state
    service = auth_service or AuthSessionService()
    while True:
        try:
            checked_at = datetime.now(UTC)
            probe = await service.backend_readiness_probe_async()
            state = backend_readiness_state_after_probe(state, probe, checked_at=checked_at)
            _write_state(settings, state)
            _log_probe(state, probe)
        except (OSError, RuntimeError, ValueError) as exc:
            state = _state_after_crash(settings, state, exc)
        await asyncio.sleep(settings.heartbeat_interval_seconds)


def _write_state(settings: ReadinessSettings, state: BackendReadinessState) -> None:
    try:
        write_backend_readiness_state(settings.state_file, state)
    except OSError as exc:
        log_event(
            _LOGGER,
            logging.WARNING,
            "enji_backend_readiness_state_write_failed",
            {"code": "STORAGE", "message": str(exc)},
        )


def _log_probe(state: BackendReadinessState, probe: BackendReadinessProbe) -> None:
    event = "enji_backend_readiness_succeeded" if state.ready else "enji_backend_readiness_failed"
    level = logging.INFO if state.ready else logging.WARNING
    fields: dict[str, object] = {
        "checked_at": state.checked_at,
        "credential_type": state.credential_type,
        "elapsed_ms": probe.elapsed_ms,
    }
    if not state.ready:
        fields.update(
            {
                "failure_kind": state.failure_kind,
                "code": state.failure_code,
                "status_code": state.failure_status_code,
                "message": state.failure_message,
                "consecutive_failures": state.consecutive_failures,
            }
        )
    log_event(_LOGGER, level, event, fields)


def _state_after_crash(
    settings: ReadinessSettings, previous: BackendReadinessState, exc: Exception
) -> BackendReadinessState:
    probe = BackendReadinessProbe(
        ready=False, failure_kind="internal", failure_code=type(exc).__name__, failure_message=str(exc)
    )
    state = backend_readiness_state_after_probe(previous, probe, checked_at=datetime.now(UTC))
    _write_state(settings, state)
    log_event(
        _LOGGER,
        logging.ERROR,
        "enji_backend_readiness_probe_crashed",
        {"error_type": type(exc).__name__, "message": str(exc), "consecutive_failures": state.consecutive_failures},
    )
    return state


async def _cancel_tasks(tasks: set[asyncio.Task[None]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task


__all__ = [
    "RuntimeSupervisor",
    "run_service",
    "run_service_async",
    "start_backend_readiness_task",
    "supervise_tasks",
]
