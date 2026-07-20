import asyncio
import logging
import random
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeGuard

from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_never
from tenacity.wait import wait_base

from enji_guard_cli.auth_session.store import StoredAuth

AUTO_REFRESH_MAX_RETRY_SECONDS = 3600.0


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


class _AuthRefreshWait(wait_base):
    def __init__(self, settings: AutoRefreshSettingsLike) -> None:
        self._settings = settings

    def __call__(self, retry_state: RetryCallState) -> float:
        exception = retry_state.outcome.exception() if retry_state.outcome is not None else None
        attempt_number = int(retry_state.attempt_number)
        if getattr(exception, "code", None) == "AUTH_REQUIRED":
            base = float(self._settings.auth_required_retry_seconds)
        else:
            exponent = max(attempt_number - 1, 0)
            growth = float(self._settings.retry_initial_seconds) * (2.0**exponent)
            cap = min(float(self._settings.retry_max_seconds), AUTO_REFRESH_MAX_RETRY_SECONDS)
            base = min(cap, growth)
        jitter = random.uniform(0.0, self._settings.retry_jitter_seconds)  # noqa: S311 - non-secret delay jitter
        return min(base + jitter, AUTO_REFRESH_MAX_RETRY_SECONDS)


class AuthSessionResilience:
    """Supervise cookie-session recovery with bounded, classified Tenacity backoff."""

    def __init__(self, settings: AutoRefreshSettingsLike, dependencies: AutoRefreshLoopDependencies) -> None:
        self._settings = settings
        self._dependencies = dependencies

    async def refresh(self, operation: Callable[[], Awaitable[StoredAuth]]) -> StoredAuth:
        retrying = AsyncRetrying(
            retry=retry_if_exception(_is_resilience_retryable),
            wait=_AuthRefreshWait(self._settings),
            stop=stop_never,
            reraise=True,
            before_sleep=self._before_sleep,
        )
        async for attempt in retrying:
            with attempt:
                return await operation()
        raise RuntimeError("auth refresh resilience stopped unexpectedly")

    def _before_sleep(self, state: RetryCallState) -> None:
        exception = state.outcome.exception() if state.outcome is not None else None
        code = getattr(exception, "code", None)
        retry_class = "auth_required" if code == "AUTH_REQUIRED" else "storage_or_upstream"
        delay = state.next_action.sleep if state.next_action is not None else 0.0
        self._dependencies.log_event_fn(
            self._dependencies.logger,
            logging.WARNING,
            "enji_auth_auto_refresh_retry",
            {
                "profile": "AUTH_REFRESH",
                "attempt": state.attempt_number,
                "delay_seconds": delay,
                "retry_class": retry_class,
                "code": code,
                "status_code": getattr(exception, "status_code", None),
            },
        )


async def _auto_refresh_loop(
    *,
    auth_file: Path,
    refresh_settings: AutoRefreshSettingsLike,
    dependencies: AutoRefreshLoopDependencies,
) -> None:
    async with dependencies.client_factory() as client:
        resilience = AuthSessionResilience(refresh_settings, dependencies)
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
                refreshed_auth = await resilience.refresh(
                    lambda: dependencies.refresh_stored_cookie_auth_fn(auth_file, client)
                )
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


def _is_resilience_retryable(exc: BaseException) -> bool:
    return isinstance(exc, Exception)


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
