import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeGuard

from enji_guard_cli.auth_session.store import StoredAuth
from enji_guard_cli.transport import EnjiHttpError


class AutoRefreshSettingsLike(Protocol):
    @property
    def enabled(self) -> bool: ...

    @property
    def lead_seconds(self) -> int: ...

    @property
    def fallback_seconds(self) -> int: ...

    @property
    def retry_seconds(self) -> int: ...

    @property
    def retry_initial_seconds(self) -> float: ...

    @property
    def retry_max_seconds(self) -> float: ...

    @property
    def retry_jitter_seconds(self) -> float: ...

    @property
    def auth_required_retry_seconds(self) -> int: ...


class RefreshErrorLike(Protocol):
    code: str
    status_code: int | None


@dataclass(frozen=True)
class AutoRefreshLoopDependencies:
    sleep_seconds_fn: Callable[..., int]
    load_sleep_seconds_stored_auth_fn: Callable[[Path], StoredAuth | None]
    cookie_refresh_sleep_seconds_fn: Callable[..., int]
    refresh_stored_cookie_auth_fn: Callable[[Path, object], Awaitable[StoredAuth]]
    cookie_access_expires_at_fn: Callable[[StoredAuth], datetime | None]
    is_refresh_error_fn: Callable[[Exception], TypeGuard[RefreshErrorLike]]
    log_event_fn: Callable[..., None]
    logger: logging.Logger
    client_factory: Callable[[], AbstractAsyncContextManager[object]]
    credential_changes_fn: Callable[[Path], AsyncGenerator[None]]
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
        changes = dependencies.credential_changes_fn(auth_file)
        change_task = asyncio.ensure_future(anext(changes))
        try:
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
                        {"error_type": type(exc).__name__, "retry_seconds": refresh_settings.retry_seconds},
                    )
                    if await _wait_for_credential_change(
                        change_task, refresh_settings.retry_seconds, dependencies.sleep_fn
                    ):
                        change_task = asyncio.ensure_future(anext(changes))
                    continue
                dependencies.log_event_fn(
                    dependencies.logger,
                    logging.INFO,
                    "enji_auth_auto_refresh_scheduled",
                    {"sleep_seconds": sleep_seconds, "auth_file": str(auth_file)},
                )
                if await _wait_for_credential_change(change_task, sleep_seconds, dependencies.sleep_fn):
                    change_task = asyncio.ensure_future(anext(changes))
                    continue
                try:
                    refreshed_auth = await dependencies.refresh_stored_cookie_auth_fn(auth_file, client)
                except EnjiHttpError as exc:
                    dependencies.log_event_fn(
                        dependencies.logger,
                        logging.WARNING,
                        "enji_auth_auto_refresh_blocked",
                        {
                            "code": exc.code,
                            "status_code": exc.status_code,
                            "retry_seconds": refresh_settings.retry_seconds,
                        },
                    )
                    if await _wait_for_credential_change(
                        change_task, refresh_settings.retry_seconds, dependencies.sleep_fn
                    ):
                        change_task = asyncio.ensure_future(anext(changes))
                    continue
                expires_at = dependencies.cookie_access_expires_at_fn(refreshed_auth)
                dependencies.log_event_fn(
                    dependencies.logger,
                    logging.INFO,
                    "enji_auth_auto_refresh_succeeded",
                    {"access_expires_at": expires_at.isoformat() if expires_at is not None else None},
                )
        finally:
            change_task.cancel()
            await asyncio.gather(change_task, return_exceptions=True)
            await changes.aclose()


async def _wait_for_credential_change(
    change_task: asyncio.Future[None],
    timeout_seconds: float,
    sleep_fn: Callable[[float], Awaitable[None]],
) -> bool:
    sleep_task = asyncio.ensure_future(sleep_fn(timeout_seconds))
    try:
        done, _pending = await asyncio.wait({change_task, sleep_task}, return_when=asyncio.FIRST_COMPLETED)
        if change_task not in done:
            sleep_task.result()
            return False
        change_task.result()
        return True
    finally:
        if not sleep_task.done():
            sleep_task.cancel()
            await asyncio.gather(sleep_task, return_exceptions=True)


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
