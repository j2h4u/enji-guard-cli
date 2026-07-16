import json
import logging
import random
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol, Self, cast
from urllib.parse import urlsplit

import httpx
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_base

from enji_guard_cli.runtime_observability.telemetry import log_event
from enji_guard_cli.settings import default_settings
from enji_guard_cli.transport_types import RetryProfile

RATE_LIMIT_STATUS_CODE = 429
_LOGGER = logging.getLogger(__name__)
_TRANSPORT_SETTINGS = default_settings().transport

type EnjiJsonScalar = None | bool | int | float | str
type EnjiJsonValue = EnjiJsonScalar | list[EnjiJsonValue] | dict[str, EnjiJsonValue]


@dataclass(frozen=True, slots=True)
class RetryConfig:
    total: int = _TRANSPORT_SETTINGS.retry.total
    backoff_factor: float = _TRANSPORT_SETTINGS.retry.backoff_factor
    max_delay_seconds: float = _TRANSPORT_SETTINGS.retry.max_delay_seconds
    jitter_seconds: float = _TRANSPORT_SETTINGS.retry.jitter_seconds
    status_forcelist: tuple[int, ...] = _TRANSPORT_SETTINGS.retry.retryable_status_codes
    respect_retry_after_header: bool = _TRANSPORT_SETTINGS.retry.respect_retry_after_header

    def build(self) -> Self:
        """Keep a small policy object for callers; execution is owned by Tenacity below."""
        return self


@dataclass(frozen=True, slots=True)
class EnjiHttpRequest:
    method: str
    url: str
    operation: str
    headers: Mapping[str, str]
    profile: RetryProfile = RetryProfile.READ
    json_body: EnjiJsonValue | None = None
    timeout_seconds: float = _TRANSPORT_SETTINGS.timeout_seconds


@dataclass(frozen=True, slots=True)
class EnjiHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    set_cookie_headers: tuple[str, ...] = ()

    def json(self, *, operation: str) -> object | None:
        if not self.content:
            return None
        try:
            return cast(object, json.loads(self.content))
        except json.JSONDecodeError as exc:
            raise EnjiResponseDecodeError(operation) from exc


class EnjiHttpClient(Protocol):
    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse: ...


class EnjiHttpError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class EnjiTransportError(EnjiHttpError):
    def __init__(self, operation: str, cause: httpx.HTTPError) -> None:
        super().__init__("UPSTREAM", f"{operation} request failed: {cause}")


class EnjiUnexpectedStatusError(EnjiHttpError):
    def __init__(self, operation: str, status_code: int) -> None:
        super().__init__("UPSTREAM", f"{operation} failed with HTTP {status_code}", status_code=status_code)


class EnjiRateLimitError(EnjiHttpError):
    def __init__(self, operation: str, *, retry_after_seconds: int | None) -> None:
        super().__init__(
            "RATE_LIMIT",
            _rate_limit_message(operation, retry_after_seconds),
            status_code=RATE_LIMIT_STATUS_CODE,
            retry_after_seconds=retry_after_seconds,
        )


class EnjiResponseDecodeError(EnjiHttpError):
    def __init__(self, operation: str) -> None:
        super().__init__("UPSTREAM", f"{operation} returned invalid JSON")


class _RetryableResponseError(Exception):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


class HttpxEnjiHttpClient:
    """The sole real HTTP executor, including profile-aware Tenacity retries."""

    def __init__(self, client: httpx.AsyncClient | None = None, *, retry_config: RetryConfig | None = None) -> None:
        self._owned_client = client is None
        self._client = client if client is not None else httpx.AsyncClient(follow_redirects=False)
        self._retry_config = retry_config or RetryConfig()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
        started_at = time.perf_counter()
        response: httpx.Response | None = None
        try:
            if not request.profile.can_retry or self._retry_config.total <= 0:
                response = await self._request_once(request)
            else:
                retrying = AsyncRetrying(
                    retry=retry_if_exception(_is_retryable_attempt),
                    wait=_RetryWait(self._retry_config),
                    stop=stop_after_attempt(self._retry_config.total + 1),
                    reraise=True,
                    before_sleep=lambda state: _log_retry(request, state),
                )
                try:
                    async for attempt in retrying:
                        with attempt:
                            response = await self._request_once(request)
                except _RetryableResponseError as exc:
                    response = exc.response
        except httpx.HTTPError as exc:
            _log_http_error(request, started_at, exc, attempt=self._retry_config.total + 1)
            raise EnjiTransportError(request.operation, exc) from exc

        assert response is not None
        _log_http_response(
            request,
            started_at,
            response.status_code,
            attempt=_attempt_count(request, self._retry_config, response),
        )
        return _response_from_httpx(response)

    async def _request_once(self, request: EnjiHttpRequest) -> httpx.Response:
        if request.json_body is None:
            response = await self._client.request(
                request.method,
                request.url,
                headers=dict(request.headers),
                timeout=request.timeout_seconds,
            )
        else:
            response = await self._client.request(
                request.method,
                request.url,
                headers=dict(request.headers),
                json=request.json_body,
                timeout=request.timeout_seconds,
            )
        if request.profile.can_retry and response.status_code in self._retry_config.status_forcelist:
            raise _RetryableResponseError(response)
        return response


class _RetryWait(wait_base):
    def __init__(self, config: RetryConfig) -> None:
        self._config = config

    def __call__(self, retry_state: RetryCallState) -> float:
        retry_after = _retry_after_from_attempt(retry_state)
        if retry_after is not None and self._config.respect_retry_after_header:
            return min(float(retry_after), self._config.max_delay_seconds)
        exponent = max(int(retry_state.attempt_number) - 1, 0)
        growth = float(self._config.backoff_factor) * (2.0**exponent)
        cap = float(self._config.max_delay_seconds)
        delay = min(cap, growth)
        jitter = random.uniform(0.0, float(self._config.jitter_seconds))  # noqa: S311 - non-secret delay jitter
        total = delay + jitter
        return min(cap, total)


def raise_for_response_status(
    response: EnjiHttpResponse,
    *,
    operation: str,
    expected_statuses: Collection[int],
) -> None:
    if response.status_code in expected_statuses:
        return
    if response.status_code == RATE_LIMIT_STATUS_CODE:
        raise EnjiRateLimitError(operation, retry_after_seconds=retry_after_seconds(response.headers))
    raise EnjiUnexpectedStatusError(operation, response.status_code)


def retry_after_seconds(headers: Mapping[str, str]) -> int | None:
    raw_value = headers.get("retry-after")
    if raw_value is None:
        raw_value = headers.get("Retry-After")
    if raw_value is None:
        return None
    return _parse_retry_after(raw_value)


def _is_retryable_attempt(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPError | _RetryableResponseError)


def _retry_after_from_attempt(retry_state: RetryCallState) -> int | None:
    if retry_state.outcome is None:
        return None
    exception = retry_state.outcome.exception()
    if not isinstance(exception, _RetryableResponseError):
        return None
    return retry_after_seconds(dict(exception.response.headers))


def _log_retry(request: EnjiHttpRequest, retry_state: RetryCallState) -> None:
    exception = retry_state.outcome.exception() if retry_state.outcome is not None else None
    retry_class = "status" if isinstance(exception, _RetryableResponseError) else "transport"
    delay = retry_state.next_action.sleep if retry_state.next_action is not None else 0.0
    log_event(
        _LOGGER,
        logging.WARNING,
        "enji_http_retry",
        {
            "operation": request.operation,
            "method": request.method,
            "path": _url_path(request.url),
            "profile": request.profile.value,
            "attempt": retry_state.attempt_number,
            "delay_seconds": delay,
            "retry_class": retry_class,
        },
    )


def _log_http_response(request: EnjiHttpRequest, started_at: float, status_code: int, *, attempt: int) -> None:
    log_event(
        _LOGGER,
        logging.INFO,
        "enji_http_response",
        {
            "operation": request.operation,
            "method": request.method,
            "path": _url_path(request.url),
            "profile": request.profile.value,
            "attempt": attempt,
            "delay_seconds": 0.0,
            "retry_class": "none",
            "status_code": status_code,
            "elapsed_ms": _elapsed_ms(started_at),
        },
    )


def _log_http_error(request: EnjiHttpRequest, started_at: float, exc: httpx.HTTPError, *, attempt: int) -> None:
    log_event(
        _LOGGER,
        logging.WARNING,
        "enji_http_error",
        {
            "operation": request.operation,
            "method": request.method,
            "path": _url_path(request.url),
            "profile": request.profile.value,
            "attempt": attempt,
            "delay_seconds": 0.0,
            "retry_class": "transport",
            "error_type": exc.__class__.__name__,
            "elapsed_ms": _elapsed_ms(started_at),
        },
    )


def _response_from_httpx(response: httpx.Response) -> EnjiHttpResponse:
    return EnjiHttpResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        content=response.content,
        set_cookie_headers=tuple(response.headers.get_list("set-cookie")),
    )


def _attempt_count(request: EnjiHttpRequest, config: RetryConfig, response: httpx.Response) -> int:
    if not request.profile.can_retry or config.total <= 0:
        return 1
    return config.total + 1 if response.status_code in config.status_forcelist else 1


def _url_path(url: str) -> str:
    return urlsplit(url).path or "/"


def _elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _parse_retry_after(raw_value: str) -> int | None:
    stripped = raw_value.strip()
    if stripped.isdigit():
        return int(stripped)
    try:
        parsed = parsedate_to_datetime(stripped)
    except TypeError, ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta = int((parsed - datetime.now(UTC)).total_seconds())
    return max(delta, 0)


def _rate_limit_message(operation: str, retry_after_seconds: int | None) -> str:
    if retry_after_seconds is None:
        return f"{operation} was rate limited"
    return f"{operation} was rate limited; retry after {retry_after_seconds}s"
