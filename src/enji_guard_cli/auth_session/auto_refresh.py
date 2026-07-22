import asyncio
import logging
import random
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from enji_guard_cli.auth_session.coordinator import PreDispatchLocalError, TerminalRevisionRequiredError
from enji_guard_cli.auth_session.store import AuthLoaded, StoredAuth, load_auth


def _stored_auth_revision(auth_file: Path) -> str | None:
    loaded = load_auth(auth_file)
    if isinstance(loaded, AuthLoaded):
        return loaded.auth["revision"]
    return None


class AutoRefreshSettingsLike(Protocol):
    @property
    def enabled(self) -> bool: ...

    @property
    def lead_seconds(self) -> int: ...

    @property
    def fallback_seconds(self) -> int: ...

    @property
    def revision_poll_seconds(self) -> float: ...

    @property
    def pre_dispatch_retry_limit(self) -> int: ...

    @property
    def pre_dispatch_retry_initial_seconds(self) -> float: ...

    @property
    def pre_dispatch_retry_max_seconds(self) -> float: ...

    @property
    def pre_dispatch_retry_jitter_seconds(self) -> float: ...


@dataclass(frozen=True)
class AutoRefreshLoopDependencies:
    sleep_seconds_fn: Callable[..., int]
    load_sleep_seconds_stored_auth_fn: Callable[[Path], StoredAuth | None]
    cookie_refresh_sleep_seconds_fn: Callable[..., int]
    refresh_stored_cookie_auth_fn: Callable[[Path, object], Awaitable[StoredAuth]]
    log_event_fn: Callable[..., None]
    logger: logging.Logger
    client_factory: Callable[[], AbstractAsyncContextManager[object]]
    credential_changes_fn: Callable[[Path], AsyncGenerator[None]]
    revision_reader: Callable[[Path], str | None] = _stored_auth_revision
    monotonic_fn: Callable[[], float] = time.monotonic
    random_fn: Callable[[], float] = random.random
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep


@dataclass(frozen=True)
class AutoRefreshTaskDependencies:
    load_stored_auth_fn: Callable[[Path], StoredAuth | None]
    auto_refresh_loop_fn: Callable[..., Coroutine[object, object, None]]
    loop_dependencies: AutoRefreshLoopDependencies


async def _auto_refresh_loop(
    *,
    auth_file: Path,
    refresh_settings: AutoRefreshSettingsLike,
    dependencies: AutoRefreshLoopDependencies,
) -> None:
    async with dependencies.client_factory() as client:
        retry_count = 0
        while True:
            try:
                sleep_seconds = dependencies.sleep_seconds_fn(
                    auth_file=auth_file,
                    refresh_settings=refresh_settings,
                    load_stored_auth_fn=dependencies.load_sleep_seconds_stored_auth_fn,
                    cookie_refresh_sleep_seconds_fn=dependencies.cookie_refresh_sleep_seconds_fn,
                )
            except (OSError, ValueError) as exc:
                dependencies.log_event_fn(
                    dependencies.logger,
                    logging.ERROR,
                    "enji_auth_auto_refresh_schedule_failed",
                    {"error_type": type(exc).__name__},
                )
                await _wait_for_credential_change(
                    auth_file=auth_file,
                    expected_revision=dependencies.revision_reader(auth_file),
                    timeout_seconds=refresh_settings.fallback_seconds,
                    poll_seconds=refresh_settings.revision_poll_seconds,
                    dependencies=dependencies,
                )
                continue

            dependencies.log_event_fn(
                dependencies.logger,
                logging.INFO,
                "enji_auth_auto_refresh_scheduled",
                {"sleep_seconds": sleep_seconds, "auth_file": str(auth_file)},
            )
            expected_revision = dependencies.revision_reader(auth_file)
            changed = await _wait_for_credential_change(
                auth_file=auth_file,
                expected_revision=expected_revision,
                timeout_seconds=sleep_seconds,
                poll_seconds=refresh_settings.revision_poll_seconds,
                dependencies=dependencies,
            )
            if changed:
                retry_count = 0
                continue

            try:
                await dependencies.refresh_stored_cookie_auth_fn(auth_file, client)
            except PreDispatchLocalError as exc:
                if retry_count >= refresh_settings.pre_dispatch_retry_limit:
                    dependencies.log_event_fn(
                        dependencies.logger,
                        logging.ERROR,
                        "enji_auth_auto_refresh_pre_dispatch_retry_exhausted",
                        {"error_type": type(exc.__cause__).__name__},
                    )
                    retry_count = 0
                    await _wait_for_credential_change(
                        auth_file=auth_file,
                        expected_revision=expected_revision,
                        timeout_seconds=refresh_settings.fallback_seconds,
                        poll_seconds=refresh_settings.revision_poll_seconds,
                        dependencies=dependencies,
                    )
                    continue
                retry_count += 1
                retry_seconds = _pre_dispatch_retry_seconds(refresh_settings, retry_count, dependencies.random_fn)
                dependencies.log_event_fn(
                    dependencies.logger,
                    logging.WARNING,
                    "enji_auth_auto_refresh_pre_dispatch_retry",
                    {
                        "attempt": retry_count,
                        "delay_seconds": retry_seconds,
                        "error_type": type(exc.__cause__).__name__,
                    },
                )
                if await _wait_for_credential_change(
                    auth_file=auth_file,
                    expected_revision=expected_revision,
                    timeout_seconds=retry_seconds,
                    poll_seconds=refresh_settings.revision_poll_seconds,
                    dependencies=dependencies,
                ):
                    retry_count = 0
                continue
            except TerminalRevisionRequiredError as exc:
                retry_count = 0
                await _wait_until_revision_changes(
                    auth_file=auth_file,
                    source_revision=exc.source_revision,
                    refresh_settings=refresh_settings,
                    dependencies=dependencies,
                )
                continue
            retry_count = 0


async def _wait_until_revision_changes(
    *,
    auth_file: Path,
    source_revision: str,
    refresh_settings: AutoRefreshSettingsLike,
    dependencies: AutoRefreshLoopDependencies,
) -> None:
    while not await _wait_for_credential_change(
        auth_file=auth_file,
        expected_revision=source_revision,
        timeout_seconds=refresh_settings.fallback_seconds,
        poll_seconds=refresh_settings.revision_poll_seconds,
        dependencies=dependencies,
    ):
        pass


async def _wait_for_credential_change(
    *,
    auth_file: Path,
    expected_revision: str | None,
    timeout_seconds: float,
    poll_seconds: float,
    dependencies: AutoRefreshLoopDependencies,
) -> bool:
    """Wait monotonically for a watcher wake-up or a changed durable revision.

    The watcher is purposely disposable: a watcher error is logged and this
    wait continues by bounded revision polling, which also covers missed
    bind-mount events.
    """

    changes = dependencies.credential_changes_fn(auth_file)
    watcher_task = asyncio.create_task(anext(changes))
    deadline = dependencies.monotonic_fn() + timeout_seconds
    try:
        while True:
            if dependencies.revision_reader(auth_file) != expected_revision:
                return True
            remaining_seconds = deadline - dependencies.monotonic_fn()
            if remaining_seconds <= 0:
                return False
            interval = min(poll_seconds, remaining_seconds)
            sleep_task = asyncio.ensure_future(dependencies.sleep_fn(interval))
            waitables: set[asyncio.Task[object]] = {sleep_task}
            if watcher_task is not None:
                waitables.add(watcher_task)
            try:
                done, _pending = await asyncio.wait(waitables, return_when=asyncio.FIRST_COMPLETED)
                if watcher_task is not None and watcher_task in done:
                    if _watcher_changed(watcher_task, dependencies):
                        return True
                    watcher_task = None
                if sleep_task in done:
                    sleep_task.result()
            finally:
                if not sleep_task.done():
                    sleep_task.cancel()
                    await asyncio.gather(sleep_task, return_exceptions=True)
    finally:
        if watcher_task is not None and not watcher_task.done():
            watcher_task.cancel()
            await asyncio.gather(watcher_task, return_exceptions=True)
        await changes.aclose()


def _watcher_changed(watcher_task: asyncio.Task[None], dependencies: AutoRefreshLoopDependencies) -> bool:
    try:
        watcher_task.result()
    except asyncio.CancelledError:
        raise
    except (OSError, RuntimeError, StopAsyncIteration, ValueError) as exc:
        # An external filesystem watcher has no correctness authority.  Its
        # full exception taxonomy is intentionally contained at this boundary.
        dependencies.log_event_fn(
            dependencies.logger,
            logging.WARNING,
            "enji_auth_credential_watcher_failed",
            {"error_type": type(exc).__name__},
        )
        return False
    return True


def _pre_dispatch_retry_seconds(
    settings: AutoRefreshSettingsLike,
    retry_count: int,
    random_fn: Callable[[], float],
) -> float:
    exponential_seconds = float(settings.pre_dispatch_retry_initial_seconds) * (2.0 ** (retry_count - 1))
    jitter_seconds = float(settings.pre_dispatch_retry_jitter_seconds) * random_fn()
    return min(float(settings.pre_dispatch_retry_max_seconds), exponential_seconds + jitter_seconds)


def _auto_refresh_sleep_seconds(
    *,
    auth_file: Path,
    refresh_settings: AutoRefreshSettingsLike,
    load_stored_auth_fn: Callable[[Path], StoredAuth | None],
    cookie_refresh_sleep_seconds_fn: Callable[..., int],
) -> int:
    stored_auth = load_stored_auth_fn(auth_file)
    if stored_auth is None:
        return refresh_settings.fallback_seconds
    return cookie_refresh_sleep_seconds_fn(stored_auth, datetime.now(UTC), settings=refresh_settings)


def start_auto_refresh_task(
    *,
    auth_file: Path,
    refresh_settings: AutoRefreshSettingsLike,
    credential_cookie_type: str,
    dependencies: AutoRefreshTaskDependencies,
) -> asyncio.Task[None] | None:
    if not refresh_settings.enabled:
        return None
    stored_auth = dependencies.load_stored_auth_fn(auth_file)
    if stored_auth is not None and stored_auth["credential"]["type"] != credential_cookie_type:
        return None
    return asyncio.create_task(
        dependencies.auto_refresh_loop_fn(
            auth_file=auth_file,
            refresh_settings=refresh_settings,
            dependencies=dependencies.loop_dependencies,
        ),
        name="enji-guard-auth-auto-refresh",
    )
