import asyncio
import logging
import secrets
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from enji_guard_cli.auth_session.coordinator import (
    PostDispatchPersistenceError,
    PreDispatchLocalError,
    RefreshCoordinator,
    RetainedRefreshSuccessor,
    RetainedSuccessorProjected,
    RetainedSuccessorProjection,
    RetainedSuccessorSuperseded,
    TerminalRevisionRequiredError,
)
from enji_guard_cli.auth_session.store import (
    AuthAbsent,
    AuthClockAnomaly,
    AuthCorrupt,
    AuthIoFailure,
    AuthLoaded,
    AuthLoadResult,
    AuthUnsupported,
    StoredAuth,
    load_auth,
)
from enji_guard_cli.transport import EnjiHttpResponse

_JITTER_RANDOM = secrets.SystemRandom()


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
    def adjudication_poll_seconds(self) -> int: ...

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


class _NoopExchange:
    async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
        del source
        raise AssertionError("retained successor projection must not dispatch")


def _project_retained_successor_default(
    auth_file: Path, retained_successor: RetainedRefreshSuccessor
) -> RetainedSuccessorProjection:
    return RefreshCoordinator(auth_file, _NoopExchange()).project_retained_successor(retained_successor)


@dataclass(frozen=True)
class AutoRefreshLoopDependencies:
    load_auth_fn: Callable[[Path], AuthLoadResult]
    cookie_refresh_sleep_seconds_fn: Callable[..., int]
    refresh_cookie_auth_fn: Callable[[Path, StoredAuth, object], Awaitable[StoredAuth]]
    adjudicate_unknown_outcome_fn: Callable[[Path, object], Awaitable[bool]]
    log_event_fn: Callable[..., None]
    logger: logging.Logger
    client_factory: Callable[[], AbstractAsyncContextManager[object]]
    credential_changes_fn: Callable[[Path], AsyncGenerator[None]]
    project_retained_successor_fn: Callable[[Path, RetainedRefreshSuccessor], RetainedSuccessorProjection] = (
        _project_retained_successor_default
    )
    revision_reader: Callable[[Path], str | None] = _stored_auth_revision
    monotonic_fn: Callable[[], float] = time.monotonic
    random_fn: Callable[[], float] = _JITTER_RANDOM.random
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep


class RetainedSuccessorBuffer:
    """One in-memory successor awaiting durable projection.

    This is intentionally process-local.  It protects the long-lived refresh
    control plane from temporary storage failures; it is not a general auth
    store and is lost on restart.
    """

    def __init__(self) -> None:
        self._retained_successor: RetainedRefreshSuccessor | None = None

    def get(self) -> RetainedRefreshSuccessor | None:
        if self._retained_successor is None:
            return None
        return self._retained_successor.snapshot()

    def remember(self, retained_successor: RetainedRefreshSuccessor) -> None:
        self._retained_successor = retained_successor.snapshot()

    def clear(self, retained_successor: RetainedRefreshSuccessor) -> None:
        current = self._retained_successor
        if (
            current is not None
            and current.source_revision == retained_successor.source_revision
            and current.auth["revision"] == retained_successor.auth["revision"]
        ):
            self._retained_successor = None


@dataclass(frozen=True)
class AutoRefreshTaskDependencies:
    auto_refresh_loop_fn: Callable[..., Coroutine[object, object, None]]
    loop_dependencies: AutoRefreshLoopDependencies


@dataclass(frozen=True, slots=True)
class _ScheduledCookieAuth:
    auth: StoredAuth
    expected_revision: str


@dataclass(frozen=True, slots=True)
class _PreDispatchRetry:
    auth_file: Path
    expected_revision: str
    retry_count: int
    exc: PreDispatchLocalError


async def _auto_refresh_loop(
    *,
    auth_file: Path,
    refresh_settings: AutoRefreshSettingsLike,
    dependencies: AutoRefreshLoopDependencies,
) -> None:
    async with dependencies.client_factory() as client:
        retry_count = 0
        projection_retry_count = 0
        retained_successors = RetainedSuccessorBuffer()
        while True:
            retained_successor = retained_successors.get()
            if retained_successor is not None:
                projected = await _project_retained_successor(
                    auth_file=auth_file,
                    retained_successor=retained_successor,
                    retry_count=projection_retry_count,
                    refresh_settings=refresh_settings,
                    dependencies=dependencies,
                )
                if projected:
                    retained_successors.clear(retained_successor)
                    projection_retry_count = 0
                    retry_count = 0
                else:
                    projection_retry_count += 1
                continue

            scheduled = await _scheduled_cookie_auth(
                auth_file=auth_file,
                refresh_settings=refresh_settings,
                dependencies=dependencies,
            )
            if scheduled is None:
                retry_count = 0
                continue

            try:
                await dependencies.refresh_cookie_auth_fn(auth_file, scheduled.auth, client)
            except PreDispatchLocalError as exc:
                retry_count = await _handle_pre_dispatch_error(
                    _PreDispatchRetry(auth_file, scheduled.expected_revision, retry_count, exc),
                    refresh_settings=refresh_settings,
                    dependencies=dependencies,
                )
                continue
            except PostDispatchPersistenceError as exc:
                retry_count = 0
                projection_retry_count = 0
                retained_successors.remember(exc.retained_successor)
                dependencies.log_event_fn(
                    dependencies.logger,
                    logging.WARNING,
                    "enji_auth_auto_refresh_retained_after_storage_failure",
                    {"source_revision": exc.source_revision, "error_type": type(exc.__cause__).__name__},
                )
                continue
            except TerminalRevisionRequiredError as exc:
                retry_count = 0
                await _wait_until_revision_changes(
                    auth_file=auth_file,
                    source_revision=exc.source_revision,
                    refresh_settings=refresh_settings,
                    dependencies=dependencies,
                    client=client,
                )
                continue
            retry_count = 0


async def _scheduled_cookie_auth(
    *,
    auth_file: Path,
    refresh_settings: AutoRefreshSettingsLike,
    dependencies: AutoRefreshLoopDependencies,
) -> _ScheduledCookieAuth | None:
    loaded = dependencies.load_auth_fn(auth_file)
    auth = _cookie_auth_or_none(loaded)
    expected_revision = _auth_revision(loaded)
    if auth is None:
        _log_unscheduled_credential(dependencies, loaded)
        await _wait_for_credential_change(
            auth_file=auth_file,
            expected_revision=expected_revision,
            timeout_seconds=refresh_settings.fallback_seconds,
            poll_seconds=refresh_settings.revision_poll_seconds,
            dependencies=dependencies,
        )
        return None

    try:
        sleep_seconds = dependencies.cookie_refresh_sleep_seconds_fn(
            stored_auth=auth, now=datetime.now(UTC), settings=refresh_settings
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
            expected_revision=expected_revision,
            timeout_seconds=refresh_settings.fallback_seconds,
            poll_seconds=refresh_settings.revision_poll_seconds,
            dependencies=dependencies,
        )
        return None

    dependencies.log_event_fn(
        dependencies.logger,
        logging.INFO,
        "enji_auth_auto_refresh_scheduled",
        {"sleep_seconds": sleep_seconds, "auth_file": str(auth_file)},
    )
    if await _wait_for_credential_change(
        auth_file=auth_file,
        expected_revision=expected_revision,
        timeout_seconds=sleep_seconds,
        poll_seconds=refresh_settings.revision_poll_seconds,
        dependencies=dependencies,
    ):
        return None

    current = dependencies.load_auth_fn(auth_file)
    current_auth = _cookie_auth_or_none(current)
    if current_auth is None or current_auth["revision"] != expected_revision:
        return None
    assert expected_revision is not None
    return _ScheduledCookieAuth(current_auth, expected_revision)


async def _handle_pre_dispatch_error(
    retry: _PreDispatchRetry,
    *,
    refresh_settings: AutoRefreshSettingsLike,
    dependencies: AutoRefreshLoopDependencies,
) -> int:
    if retry.retry_count >= refresh_settings.pre_dispatch_retry_limit:
        dependencies.log_event_fn(
            dependencies.logger,
            logging.ERROR,
            "enji_auth_auto_refresh_pre_dispatch_retry_exhausted",
            {"error_type": type(retry.exc.__cause__).__name__},
        )
        await _wait_for_credential_change(
            auth_file=retry.auth_file,
            expected_revision=retry.expected_revision,
            timeout_seconds=refresh_settings.fallback_seconds,
            poll_seconds=refresh_settings.revision_poll_seconds,
            dependencies=dependencies,
        )
        return 0
    next_retry_count = retry.retry_count + 1
    retry_seconds = _pre_dispatch_retry_seconds(refresh_settings, next_retry_count, dependencies.random_fn)
    dependencies.log_event_fn(
        dependencies.logger,
        logging.WARNING,
        "enji_auth_auto_refresh_pre_dispatch_retry",
        {
            "attempt": next_retry_count,
            "delay_seconds": retry_seconds,
            "error_type": type(retry.exc.__cause__).__name__,
        },
    )
    if await _wait_for_credential_change(
        auth_file=retry.auth_file,
        expected_revision=retry.expected_revision,
        timeout_seconds=retry_seconds,
        poll_seconds=refresh_settings.revision_poll_seconds,
        dependencies=dependencies,
    ):
        return 0
    return next_retry_count


async def _project_retained_successor(
    *,
    auth_file: Path,
    retained_successor: RetainedRefreshSuccessor,
    retry_count: int,
    refresh_settings: AutoRefreshSettingsLike,
    dependencies: AutoRefreshLoopDependencies,
) -> bool:
    try:
        projection = await asyncio.to_thread(
            dependencies.project_retained_successor_fn,
            auth_file,
            retained_successor,
        )
    except (OSError, TimeoutError) as exc:
        retry_seconds = _pre_dispatch_retry_seconds(refresh_settings, retry_count + 1, dependencies.random_fn)
        dependencies.log_event_fn(
            dependencies.logger,
            logging.WARNING,
            "enji_auth_auto_refresh_retained_projection_retry",
            {"delay_seconds": retry_seconds, "error_type": type(exc).__name__},
        )
        await _wait_for_credential_change(
            auth_file=auth_file,
            expected_revision=retained_successor.source_revision,
            timeout_seconds=retry_seconds,
            poll_seconds=refresh_settings.revision_poll_seconds,
            dependencies=dependencies,
        )
        return False
    if isinstance(projection, RetainedSuccessorProjected):
        dependencies.log_event_fn(
            dependencies.logger,
            logging.INFO,
            "enji_auth_auto_refresh_retained_projection_succeeded",
            {"source_revision": retained_successor.source_revision},
        )
        return True
    if isinstance(projection, RetainedSuccessorSuperseded):
        dependencies.log_event_fn(
            dependencies.logger,
            logging.INFO,
            "enji_auth_auto_refresh_retained_projection_superseded",
            {
                "source_revision": retained_successor.source_revision,
                "current_revision": projection.current_revision,
            },
        )
        return True
    raise AssertionError(f"unexpected retained successor projection: {type(projection).__name__}")


async def _wait_until_revision_changes(
    *,
    auth_file: Path,
    source_revision: str,
    refresh_settings: AutoRefreshSettingsLike,
    dependencies: AutoRefreshLoopDependencies,
    client: object,
) -> None:
    """Park on a terminal generation until an import or an adjudication frees it.

    A terminal outcome used to be escapable only by a credential import, which
    changes the revision this waits on.  An ambiguous outcome now has a second
    exit: the loop asks the backend what the refresh actually did, and resumes
    in place if the held credential turns out to be alive.  Deciding here rather
    than in an observer is what makes that exit work at all -- there is no other
    task to wake, because the task that must act is this one.
    """

    if await _wait_for_credential_change(
        auth_file=auth_file,
        expected_revision=source_revision,
        timeout_seconds=0,
        poll_seconds=refresh_settings.revision_poll_seconds,
        dependencies=dependencies,
    ):
        return
    if await dependencies.adjudicate_unknown_outcome_fn(auth_file, client):
        await _wait_for_credential_change(
            auth_file=auth_file,
            expected_revision=source_revision,
            timeout_seconds=refresh_settings.adjudication_poll_seconds,
            poll_seconds=refresh_settings.revision_poll_seconds,
            dependencies=dependencies,
        )
        return

    while not await _wait_for_credential_change(
        auth_file=auth_file,
        expected_revision=source_revision,
        timeout_seconds=refresh_settings.adjudication_poll_seconds,
        poll_seconds=refresh_settings.revision_poll_seconds,
        dependencies=dependencies,
    ):
        if await dependencies.adjudicate_unknown_outcome_fn(auth_file, client):
            return


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


def _cookie_auth_or_none(loaded: AuthLoadResult) -> StoredAuth | None:
    if isinstance(loaded, AuthLoaded) and loaded.auth["credential"]["type"] == "cookie":
        return loaded.auth
    return None


def _auth_revision(loaded: AuthLoadResult) -> str | None:
    if isinstance(loaded, AuthLoaded):
        return loaded.auth["revision"]
    return None


def _log_unscheduled_credential(dependencies: AutoRefreshLoopDependencies, loaded: AuthLoadResult) -> None:
    """Record a stable typed reason without leaking storage details or secrets."""

    match loaded:
        case AuthLoaded(auth=auth):
            fields: dict[str, object] = {"credential_type": auth["credential"]["type"]}
        case AuthAbsent():
            fields = {"code": "AUTH_REQUIRED"}
        case AuthCorrupt():
            fields = {"code": "AUTH_CORRUPT"}
        case AuthUnsupported():
            fields = {"code": "AUTH_UNSUPPORTED"}
        case AuthIoFailure():
            fields = {"code": "AUTH_IO_FAILURE"}
        case AuthClockAnomaly():
            fields = {"code": "AUTH_CLOCK_ANOMALY"}
        case _ as impossible:
            raise AssertionError(f"unexpected auth load result: {type(impossible).__name__}")
    dependencies.log_event_fn(
        dependencies.logger,
        logging.INFO,
        "enji_auth_auto_refresh_observing_credential",
        fields,
    )


def start_auto_refresh_task(
    *,
    auth_file: Path,
    refresh_settings: AutoRefreshSettingsLike,
    dependencies: AutoRefreshTaskDependencies,
) -> asyncio.Task[None] | None:
    if not refresh_settings.enabled:
        return None
    return asyncio.create_task(
        dependencies.auto_refresh_loop_fn(
            auth_file=auth_file,
            refresh_settings=refresh_settings,
            dependencies=dependencies.loop_dependencies,
        ),
        name="enji-guard-auth-auto-refresh",
    )
