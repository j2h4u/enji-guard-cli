"""A shared HTTP executor owned by a dedicated event-loop thread.

The operator gateways are synchronous facades and may be called from several
short-lived ``asyncio.run`` loops (for example, a bounded fanout).  The real
``httpx.AsyncClient`` therefore lives on one long-lived loop; callers bridge to
that loop rather than creating a client per request.
"""

import asyncio
import threading
from concurrent.futures import Future, wait
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
        self._closed = False
        self._startup_error: BaseException | None = None
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._executor: EnjiHttpClient | None = None
        self._inflight: set[Future[EnjiHttpResponse]] = set()
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
        with self._state_lock:
            if self._closed:
                raise RuntimeError("pooled Enji HTTP client is closed")
            loop = self._owner_loop
            executor = self._executor
        if loop is None or executor is None or loop.is_closed():
            raise RuntimeError("pooled Enji HTTP client is unavailable")
        if threading.current_thread() is self._thread:
            return await executor.request(request)
        future = asyncio.run_coroutine_threadsafe(executor.request(request), loop)
        with self._state_lock:
            if self._closed:
                future.cancel()
                raise RuntimeError("pooled Enji HTTP client is closed")
            self._inflight.add(future)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        finally:
            with self._state_lock:
                self._inflight.discard(future)

    def close(self) -> None:
        """Close the owner-loop client; safe to call repeatedly."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            loop = self._owner_loop
            inflight = tuple(self._inflight)
        if loop is None or loop.is_closed():
            return
        if threading.current_thread() is self._thread:
            shutdown_task = loop.create_task(self._shutdown_owner())
            shutdown_task.add_done_callback(lambda _: loop.stop())
            return
        wait(inflight)
        future = asyncio.run_coroutine_threadsafe(self._shutdown_owner(), loop)
        future.result()
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join()

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

    async def _initialize_owner(self) -> None:
        self._executor = HttpxEnjiHttpClient(limits=self._limits, retry_config=self._retry_config)

    async def _shutdown_owner(self) -> None:
        executor = self._executor
        if isinstance(executor, HttpxEnjiHttpClient):
            await executor.__aexit__(None, None, None)


__all__ = ["PooledEnjiHttpClient"]
