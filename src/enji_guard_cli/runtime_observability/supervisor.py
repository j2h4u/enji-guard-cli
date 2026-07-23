"""Runtime supervisor owning sibling MCP, refresh, and readiness tasks."""

import asyncio
import logging
import signal
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from enji_guard_cli.runtime_observability.ports import (
    BackendReadinessObservation,
    BackendReadinessPort,
    RuntimeAuthCoordinator,
)
from enji_guard_cli.runtime_observability.readiness import (
    BackendReadinessProbe,
    BackendReadinessState,
    backend_readiness_starting_state,
    backend_readiness_state_after_probe,
    write_backend_readiness_state,
)
from enji_guard_cli.runtime_observability.telemetry import configure_logging, log_event
from enji_guard_cli.settings import EnjiGuardSettings, ReadinessSettings, default_settings

_LOGGER = logging.getLogger(__name__)
McpTransport = Literal["stdio", "sse", "streamable-http"]


class McpServerFactory(Protocol):
    def __call__(self, host: str, port: int) -> object: ...


class McpServerRunner(Protocol):
    async def __call__(
        self, server: object, *, transport: McpTransport = "stdio", mount_path: str | None = None
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeServiceOptions:
    """Transport and listener values selected by the delivery boundary."""

    transport: McpTransport
    host: str
    port: int
    mount_path: str | None = None


@dataclass(slots=True)
class RuntimeSupervisor:
    """Injectable supervisor facade used by service composition and tests."""

    runtime_auth: RuntimeAuthCoordinator | None = None
    settings: EnjiGuardSettings | None = None

    async def run_async(
        self,
        *,
        options: RuntimeServiceOptions,
        mcp_server_factory: McpServerFactory,
        mcp_server_runner: McpServerRunner,
    ) -> None:
        await run_service_async(
            options=options,
            runtime_auth=self.runtime_auth,
            settings=self.settings,
            mcp_server_factory=mcp_server_factory,
            mcp_server_runner=mcp_server_runner,
        )


async def run_service_async(
    *,
    options: RuntimeServiceOptions,
    runtime_auth: RuntimeAuthCoordinator | None = None,
    settings: EnjiGuardSettings | None = None,
    mcp_server_factory: McpServerFactory | None = None,
    mcp_server_runner: McpServerRunner | None = None,
) -> None:
    """Start all sibling tasks and cancel them together on service exit."""
    if mcp_server_factory is None or mcp_server_runner is None:
        raise ValueError("MCP server factory and runner must be provided by delivery composition")
    if runtime_auth is not None:
        await runtime_auth.reconcile_startup()
    mcp_task = asyncio.create_task(
        mcp_server_runner(
            mcp_server_factory(options.host, options.port),
            transport=options.transport,
            mount_path=options.mount_path,
        ),
        name="enji-guard-mcp-server",
    )
    refresh_task = runtime_auth.start_background_refresh_task() if runtime_auth is not None else None
    resolved_settings = settings if settings is not None else default_settings()
    readiness_task = start_backend_readiness_task(observer=runtime_auth, settings=resolved_settings)
    shutdown_event = asyncio.Event()
    installed_signals = _install_signal_handlers(shutdown_event)
    try:
        await supervise_tasks(
            mcp_task,
            refresh_task,
            readiness_task,
            shutdown_event=shutdown_event,
            shutdown_timeout_seconds=resolved_settings.service.mcp_graceful_shutdown_timeout_seconds,
        )
    finally:
        _remove_signal_handlers(installed_signals)


def run_service(
    *,
    options: RuntimeServiceOptions,
    runtime_auth: RuntimeAuthCoordinator | None = None,
    settings: EnjiGuardSettings | None = None,
    mcp_server_factory: McpServerFactory | None = None,
    mcp_server_runner: McpServerRunner | None = None,
) -> None:
    resolved_settings = settings if settings is not None else default_settings()
    configure_logging(resolved_settings.telemetry, provenance="supervisor")
    asyncio.run(
        run_service_async(
            options=options,
            runtime_auth=runtime_auth,
            settings=resolved_settings,
            mcp_server_factory=mcp_server_factory,
            mcp_server_runner=mcp_server_runner,
        )
    )


async def supervise_tasks(
    mcp_task: asyncio.Task[None],
    refresh_task: asyncio.Task[None] | None,
    readiness_task: asyncio.Task[None] | None,
    *,
    shutdown_event: asyncio.Event | None = None,
    shutdown_timeout_seconds: float | None = None,
) -> None:
    resolved_shutdown_timeout = (
        default_settings().service.mcp_graceful_shutdown_timeout_seconds
        if shutdown_timeout_seconds is None
        else shutdown_timeout_seconds
    )
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
            await _await_mcp_shutdown(
                mcp_task,
                timeout_seconds=resolved_shutdown_timeout,
            )
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


async def _await_mcp_shutdown(mcp_task: asyncio.Task[None], *, timeout_seconds: float) -> None:
    if mcp_task.done():
        mcp_task.result()
        return
    try:
        await asyncio.wait_for(asyncio.shield(mcp_task), timeout=timeout_seconds)
    except TimeoutError:
        log_event(
            _LOGGER,
            logging.WARNING,
            "enji_guard_mcp_graceful_shutdown_timed_out",
            {"timeout_seconds": timeout_seconds},
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


def start_backend_readiness_task(
    *, observer: BackendReadinessPort | None = None, settings: EnjiGuardSettings | None = None
) -> asyncio.Task[None] | None:
    resolved_settings = settings if settings is not None else default_settings()
    readiness_settings = resolved_settings.readiness
    if not readiness_settings.enabled:
        return None
    initial_state = backend_readiness_starting_state(checked_at=datetime.now(UTC))
    _write_state(readiness_settings, initial_state)
    if observer is None:
        return None
    return asyncio.create_task(
        _backend_readiness_loop(settings=readiness_settings, initial_state=initial_state, observer=observer),
        name="enji-guard-backend-readiness",
    )


async def _backend_readiness_loop(
    *,
    settings: ReadinessSettings,
    initial_state: BackendReadinessState,
    observer: BackendReadinessPort,
) -> None:
    state = initial_state
    credential_changes, change_task = _start_credential_watcher(observer)
    try:
        while True:
            try:
                checked_at = datetime.now(UTC)
                probe = await observer.observe_backend_readiness()
                readiness_probe = _readiness_probe(probe)
                state = backend_readiness_state_after_probe(state, readiness_probe, checked_at=checked_at)
                _write_state(settings, state)
                _log_probe(state, readiness_probe)
            except (OSError, RuntimeError, ValueError) as exc:
                state = _state_after_crash(settings, state, exc)
            try:
                credential_changed = await _wait_for_readiness_trigger(change_task, settings.heartbeat_interval_seconds)
            except Exception as exc:  # noqa: BLE001 - the watcher must not stop service siblings.
                _log_credential_watcher_failure(exc)
                await _close_credential_watcher(change_task, credential_changes)
                credential_changes = None
                change_task = None
                continue
            if credential_changed:
                assert credential_changes is not None
                change_task = asyncio.create_task(anext(credential_changes))
    finally:
        await _close_credential_watcher(change_task, credential_changes)


def _start_credential_watcher(
    observer: BackendReadinessPort,
) -> tuple[AsyncGenerator[None] | None, asyncio.Task[None] | None]:
    try:
        credential_changes = observer.credential_changes()
        return credential_changes, asyncio.create_task(anext(credential_changes))
    except Exception as exc:  # noqa: BLE001 - the watcher must not stop service siblings.
        _log_credential_watcher_failure(exc)
        return None, None


async def _wait_for_readiness_trigger(change_task: asyncio.Task[None] | None, interval_seconds: int) -> bool:
    if change_task is None:
        await asyncio.sleep(interval_seconds)
        return False
    done, _ = await asyncio.wait({change_task}, timeout=interval_seconds)
    if not done:
        return False
    change_task.result()
    return True


async def _close_credential_watcher(
    change_task: asyncio.Task[None] | None, credential_changes: AsyncGenerator[None] | None
) -> None:
    if change_task is not None:
        if not change_task.done():
            change_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await change_task
    if credential_changes is not None:
        try:
            await credential_changes.aclose()
        except Exception as exc:  # noqa: BLE001 - watcher cleanup must not mask cancellation.
            _log_credential_watcher_failure(exc)


def _log_credential_watcher_failure(exc: Exception) -> None:
    log_event(
        _LOGGER,
        logging.ERROR,
        "enji_backend_readiness_credential_watcher_failed",
        {"error_type": type(exc).__name__},
    )


def _readiness_probe(observation: BackendReadinessObservation) -> BackendReadinessProbe:
    return BackendReadinessProbe(
        ready=observation.ready,
        failure_kind=observation.failure_kind,
        failure_code=observation.failure_code,
        failure_message=observation.failure_message,
        failure_status_code=observation.failure_status_code,
        credential_type=observation.credential_type,
        elapsed_ms=observation.elapsed_ms,
        bypass_grace=observation.bypass_grace,
    )


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
    "RuntimeServiceOptions",
    "RuntimeSupervisor",
    "run_service",
    "run_service_async",
    "start_backend_readiness_task",
    "supervise_tasks",
]
