"""A shared HTTP executor owned by a dedicated event-loop thread.

The operator gateways are synchronous facades and may be called from several
short-lived ``asyncio.run`` loops (for example, a bounded fanout).  The real
``httpx.AsyncClient`` therefore lives on one long-lived loop; callers bridge to
that loop rather than creating a client per request.
"""

import asyncio
import threading
from concurrent.futures import Future, wait
from contextlib import suppress
from typing import Self

import httpx

from enji_guard_cli.settings import EnjiGuardSettings, default_settings
from enji_guard_cli.transport import EnjiHttpClient, EnjiHttpRequest, EnjiHttpResponse, HttpxEnjiHttpClient, RetryConfig


class PooledEnjiHttpClient:
    """Thread-safe async bridge to one owner-loop ``HttpxEnjiHttpClient``."""

    def __init__(self, settings: EnjiGuardSettings | None = None) -> None:
        resolved_settings = settings if settings is not None else default_settings()
        pool = resolved_settings.transport.pool
        retry = resolved_settings.transport.retry
        limits = httpx.Limits(
            max_connections=pool.max_connections,
            max_keepalive_connections=pool.max_keepalive_connections,
            keepalive_expiry=pool.keepalive_expiry_seconds,
        )
        retry_config = RetryConfig(
            total=retry.total,
            backoff_factor=retry.backoff_factor,
            max_delay_seconds=retry.max_delay_seconds,
            jitter_seconds=retry.jitter_seconds,
            status_forcelist=retry.retryable_status_codes,
            respect_retry_after_header=retry.respect_retry_after_header,
        )
        self._limits = limits
        self._retry_config = retry_config
        self._state_lock = threading.Lock()
        self._ready = threading.Event()
        self._shutdown_complete = threading.Event()
        self._closed = False
        self._shutdown_error: BaseException | None = None
        self._startup_error: BaseException | None = None
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._executor: EnjiHttpClient | None = None
        self._external_inflight: set[Future[EnjiHttpResponse]] = set()
        self._owner_inflight: set[asyncio.Task[EnjiHttpResponse]] = set()
        self._thread = threading.Thread(target=self._run_owner_loop, name="enji-guard-http", daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            raise RuntimeError("failed to start pooled Enji HTTP client") from self._startup_error

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()

    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
        future: Future[EnjiHttpResponse] | None = None
        owner_task: asyncio.Task[EnjiHttpResponse] | None = None
        with self._state_lock:
            if self._closed:
                raise RuntimeError("pooled Enji HTTP client is closed")
            loop = self._owner_loop
            executor = self._executor
            if loop is None or executor is None or loop.is_closed():
                raise RuntimeError("pooled Enji HTTP client is unavailable")
            on_owner_thread = threading.current_thread() is self._thread
            if on_owner_thread:
                owner_task = asyncio.current_task(loop)
                if owner_task is None:
                    raise RuntimeError("owner-loop request is not running in an asyncio task")
                self._owner_inflight.add(owner_task)
            else:
                future = asyncio.run_coroutine_threadsafe(executor.request(request), loop)
                # Register atomically with the open-state check so close() cannot
                # miss a request that has already been submitted to the owner loop.
                self._external_inflight.add(future)
        if on_owner_thread:
            assert owner_task is not None
            try:
                return await executor.request(request)
            finally:
                with self._state_lock:
                    self._owner_inflight.discard(owner_task)
        assert future is not None
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        finally:
            with self._state_lock:
                self._external_inflight.discard(future)

    def close(self) -> None:
        """Close the owner-loop client; safe to call repeatedly."""
        external_inflight: tuple[Future[EnjiHttpResponse], ...] = ()
        owner_inflight: tuple[asyncio.Task[EnjiHttpResponse], ...] = ()
        with self._state_lock:
            if self._closed:
                is_shutdown_owner = False
                loop = None
            else:
                # Mark closed before releasing the lock, so no new request can
                # be submitted while the shutdown owner waits for existing work.
                self._closed = True
                is_shutdown_owner = True
                loop = self._owner_loop
                external_inflight = tuple(self._external_inflight)
                owner_inflight = tuple(self._owner_inflight)
        if not is_shutdown_owner:
            if threading.current_thread() is not self._thread:
                self._shutdown_complete.wait()
                self._thread.join()
                self._raise_shutdown_error()
            return
        if loop is None or loop.is_closed():
            if threading.current_thread() is not self._thread:
                self._thread.join()
            self._shutdown_complete.set()
            self._raise_shutdown_error()
            return
        if threading.current_thread() is self._thread:
            # Waiting synchronously here would deadlock the owner loop: the
            # accepted requests we must drain run on this same loop.  Defer
            # shutdown into a task which yields until their bridge futures are
            # complete instead.
            current_task = asyncio.current_task(loop)
            remaining_owner_inflight = tuple(task for task in owner_inflight if task is not current_task)
            if current_task is not None and current_task in owner_inflight:
                # This task is itself an admitted request.  It can only finish
                # after close() returns, so defer scheduling shutdown until
                # its completion rather than attempting to await itself.
                current_task.add_done_callback(
                    lambda _task: self._start_owner_shutdown(
                        loop,
                        external_inflight,
                        remaining_owner_inflight,
                    )
                )
            else:
                self._start_owner_shutdown(loop, external_inflight, remaining_owner_inflight)
            return
        try:
            wait(external_inflight)
            future = asyncio.run_coroutine_threadsafe(
                self._shutdown_after_inflight(external_inflight, owner_inflight),
                loop,
            )
            future.result()
        except BaseException as exc:  # noqa: BLE001 - replay every shutdown failure to concurrent callers
            self._record_shutdown_error(exc)
        finally:
            if not loop.is_closed():
                with suppress(RuntimeError):
                    loop.call_soon_threadsafe(loop.stop)
            self._thread.join()
            self._shutdown_complete.set()
        self._raise_shutdown_error()

    def _finish_owner_shutdown(self, task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except BaseException as exc:  # noqa: BLE001 - consume task failures before stopping the owner loop
            self._record_shutdown_error(exc)
        finally:
            loop = self._owner_loop
            if loop is not None and not loop.is_closed():
                loop.stop()

    def _start_owner_shutdown(
        self,
        loop: asyncio.AbstractEventLoop,
        external_inflight: tuple[Future[EnjiHttpResponse], ...],
        owner_inflight: tuple[asyncio.Task[EnjiHttpResponse], ...],
    ) -> None:
        if loop.is_closed():
            return
        shutdown_task = loop.create_task(self._shutdown_after_inflight(external_inflight, owner_inflight))
        shutdown_task.add_done_callback(self._finish_owner_shutdown)

    def _record_shutdown_error(self, error: BaseException) -> None:
        with self._state_lock:
            if self._shutdown_error is None:
                self._shutdown_error = error

    def _raise_shutdown_error(self) -> None:
        with self._state_lock:
            error = self._shutdown_error
        if error is not None:
            raise error

    def _run_owner_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._state_lock:
            self._owner_loop = loop
        try:
            loop.run_until_complete(self._initialize_owner())
            self._ready.set()
            loop.run_forever()
        except Exception as exc:  # noqa: BLE001 - surface owner-loop startup failures to composition
            self._startup_error = exc
            self._ready.set()
        finally:
            if not loop.is_closed():
                loop.close()
            if self._closed:
                self._shutdown_complete.set()

    async def _initialize_owner(self) -> None:
        self._executor = HttpxEnjiHttpClient(limits=self._limits, retry_config=self._retry_config)

    async def _shutdown_owner(self) -> None:
        executor = self._executor
        if isinstance(executor, HttpxEnjiHttpClient):
            await executor.__aexit__(None, None, None)

    async def _shutdown_after_inflight(
        self,
        external_inflight: tuple[Future[EnjiHttpResponse], ...],
        owner_inflight: tuple[asyncio.Task[EnjiHttpResponse], ...],
    ) -> None:
        if external_inflight:
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in external_inflight),
                return_exceptions=True,
            )
        if owner_inflight:
            await asyncio.gather(*owner_inflight, return_exceptions=True)
        await self._shutdown_owner()


__all__ = ["PooledEnjiHttpClient"]
