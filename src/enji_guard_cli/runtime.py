import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from enji_guard_cli.auth import backend_readiness_probe_async, start_auto_refresh_task
from enji_guard_cli.mcp_server import McpTransport, create_mcp_server, run_mcp_server_async
from enji_guard_cli.readiness import (
    INITIAL_BACKEND_READINESS_STATE,
    BackendReadinessProbe,
    BackendReadinessState,
    backend_readiness_state_after_probe,
    write_backend_readiness_state,
)
from enji_guard_cli.settings import ReadinessSettings, default_settings
from enji_guard_cli.telemetry import log_event

_LOGGER = logging.getLogger(__name__)


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
    readiness_task = start_backend_readiness_task()
    await _supervise_tasks(mcp_task, auto_refresh_task, readiness_task)


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
    readiness_task: asyncio.Task[None] | None,
) -> None:
    supervised_tasks = {mcp_task}
    if auto_refresh_task is not None:
        supervised_tasks.add(auto_refresh_task)
    if readiness_task is not None:
        supervised_tasks.add(readiness_task)
    try:
        done, _pending = await asyncio.wait(supervised_tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        await _cancel_tasks(supervised_tasks)


def start_backend_readiness_task() -> asyncio.Task[None] | None:
    settings = default_settings().readiness
    if not settings.enabled:
        return None
    return asyncio.create_task(
        _backend_readiness_loop(settings=settings),
        name="enji-guard-backend-readiness",
    )


async def _backend_readiness_loop(*, settings: ReadinessSettings) -> None:
    state = INITIAL_BACKEND_READINESS_STATE
    while True:
        try:
            state = await _run_backend_readiness_probe(settings=settings, previous=state)
        except (OSError, RuntimeError, ValueError) as exc:
            state = _state_after_readiness_crash(settings=settings, previous=state, exc=exc)
        await asyncio.sleep(settings.heartbeat_interval_seconds)


async def _run_backend_readiness_probe(
    *,
    settings: ReadinessSettings,
    previous: BackendReadinessState,
) -> BackendReadinessState:
    checked_at = datetime.now(UTC)
    probe = await backend_readiness_probe_async()
    state = backend_readiness_state_after_probe(previous, probe, checked_at=checked_at)
    try:
        write_backend_readiness_state(settings.state_file, state)
    except OSError as exc:
        log_event(
            _LOGGER,
            logging.WARNING,
            "enji_backend_readiness_state_write_failed",
            {"code": "STORAGE", "message": str(exc), "state_file": str(settings.state_file)},
        )
    _log_backend_readiness_probe(state, probe)
    return state


def _log_backend_readiness_probe(state: BackendReadinessState, probe: BackendReadinessProbe) -> None:
    if state.ready:
        log_event(
            _LOGGER,
            logging.INFO,
            "enji_backend_readiness_succeeded",
            {
                "checked_at": state.checked_at,
                "credential_type": state.credential_type,
                "elapsed_ms": probe.elapsed_ms,
            },
        )
        return
    log_event(
        _LOGGER,
        logging.WARNING,
        "enji_backend_readiness_failed",
        {
            "checked_at": state.checked_at,
            "failure_kind": state.failure_kind,
            "code": state.failure_code,
            "status_code": state.failure_status_code,
            "message": state.failure_message,
            "credential_type": state.credential_type,
            "consecutive_failures": state.consecutive_failures,
            "elapsed_ms": probe.elapsed_ms,
        },
    )


def _state_after_readiness_crash(
    *,
    settings: ReadinessSettings,
    previous: BackendReadinessState,
    exc: OSError | RuntimeError | ValueError,
) -> BackendReadinessState:
    probe = BackendReadinessProbe(
        ready=False,
        failure_kind="internal",
        failure_code=type(exc).__name__,
        failure_message=str(exc),
    )
    state = backend_readiness_state_after_probe(previous, probe, checked_at=datetime.now(UTC))
    try:
        write_backend_readiness_state(settings.state_file, state)
    except OSError as write_exc:
        log_event(
            _LOGGER,
            logging.WARNING,
            "enji_backend_readiness_state_write_failed",
            {"code": "STORAGE", "message": str(write_exc), "state_file": str(settings.state_file)},
        )
    log_event(
        _LOGGER,
        logging.ERROR,
        "enji_backend_readiness_probe_crashed",
        {
            "error_type": type(exc).__name__,
            "message": str(exc),
            "consecutive_failures": state.consecutive_failures,
        },
    )
    return state


async def _cancel_tasks(tasks: set[asyncio.Task[None]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
