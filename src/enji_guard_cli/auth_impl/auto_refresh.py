import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeGuard

from enji_guard_cli.auth_impl.store import StoredAuth


class AutoRefreshSettingsLike(Protocol):
    @property
    def enabled(self) -> bool: ...

    @property
    def lead_seconds(self) -> int: ...

    @property
    def fallback_seconds(self) -> int: ...

    @property
    def retry_seconds(self) -> int: ...


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
    sleep_fn: Callable[[int], Awaitable[None]] = asyncio.sleep


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
        while True:
            try:
                sleep_seconds = dependencies.sleep_seconds_fn(
                    auth_file=auth_file,
                    refresh_settings=refresh_settings,
                    load_stored_auth_fn=dependencies.load_sleep_seconds_stored_auth_fn,
                    cookie_refresh_sleep_seconds_fn=dependencies.cookie_refresh_sleep_seconds_fn,
                )
                dependencies.log_event_fn(
                    dependencies.logger,
                    logging.INFO,
                    "enji_auth_auto_refresh_scheduled",
                    {"sleep_seconds": sleep_seconds, "auth_file": str(auth_file)},
                )
                await dependencies.sleep_fn(sleep_seconds)
                refreshed_auth = await dependencies.refresh_stored_cookie_auth_fn(auth_file, client)
            except Exception as exc:
                if dependencies.is_refresh_error_fn(exc):
                    dependencies.log_event_fn(
                        dependencies.logger,
                        logging.WARNING,
                        "enji_auth_auto_refresh_failed",
                        {
                            "code": exc.code,
                            "status_code": exc.status_code,
                            "retry_seconds": refresh_settings.retry_seconds,
                        },
                    )
                    await dependencies.sleep_fn(refresh_settings.retry_seconds)
                elif isinstance(exc, OSError | ValueError):
                    dependencies.log_event_fn(
                        dependencies.logger,
                        logging.ERROR,
                        "enji_auth_auto_refresh_crashed",
                        {
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                            "retry_seconds": refresh_settings.retry_seconds,
                        },
                    )
                    await dependencies.sleep_fn(refresh_settings.retry_seconds)
                else:
                    raise
            else:
                expires_at = dependencies.cookie_access_expires_at_fn(refreshed_auth)
                dependencies.log_event_fn(
                    dependencies.logger,
                    logging.INFO,
                    "enji_auth_auto_refresh_succeeded",
                    {"access_expires_at": expires_at.isoformat() if expires_at is not None else None},
                )


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
