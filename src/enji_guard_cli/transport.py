import json
import logging
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol, Self, cast
from urllib.parse import urlsplit

import httpx
from httpx_retries import Retry, RetryTransport

from enji_guard_cli.settings import default_settings
from enji_guard_cli.telemetry import log_event

RATE_LIMIT_STATUS_CODE = 429
_LOGGER = logging.getLogger(__name__)
_TRANSPORT_SETTINGS = default_settings().transport

type EnjiJsonScalar = None | bool | int | float | str
type EnjiJsonValue = EnjiJsonScalar | list[EnjiJsonValue] | dict[str, EnjiJsonValue]


@dataclass(frozen=True, slots=True)
class RetryConfig:
    total: int = _TRANSPORT_SETTINGS.retry.total
    backoff_factor: float = _TRANSPORT_SETTINGS.retry.backoff_factor
    allowed_methods: tuple[str, ...] = _TRANSPORT_SETTINGS.retry.retryable_methods
    status_forcelist: tuple[int, ...] = _TRANSPORT_SETTINGS.retry.retryable_status_codes
    respect_retry_after_header: bool = _TRANSPORT_SETTINGS.retry.respect_retry_after_header

    def build(self) -> Retry:
        return Retry(
            total=self.total,
            backoff_factor=self.backoff_factor,
            allowed_methods=self.allowed_methods,
            status_forcelist=self.status_forcelist,
            respect_retry_after_header=self.respect_retry_after_header,
        )


@dataclass(frozen=True, slots=True)
class EnjiHttpRequest:
    method: str
    url: str
    operation: str
    headers: Mapping[str, str]
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


class HttpxEnjiHttpClient:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        retry_config: RetryConfig | None = None,
    ) -> None:
        self._owned_client = client is None
        self._client = client if client is not None else _build_async_client(retry_config or RetryConfig())

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
        started_at = time.perf_counter()
        try:
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
        except httpx.HTTPError as exc:
            _log_http_error(request, started_at, exc)
            raise EnjiTransportError(request.operation, exc) from exc
        _log_http_response(request, started_at, response.status_code)
        return EnjiHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
            set_cookie_headers=tuple(response.headers.get_list("set-cookie")),
        )


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


def _build_async_client(retry_config: RetryConfig) -> httpx.AsyncClient:
    transport = RetryTransport(retry=retry_config.build())
    return httpx.AsyncClient(follow_redirects=False, transport=transport)


def _log_http_response(request: EnjiHttpRequest, started_at: float, status_code: int) -> None:
    log_event(
        _LOGGER,
        logging.INFO,
        "enji_http_response",
        {
            "operation": request.operation,
            "method": request.method,
            "path": _url_path(request.url),
            "status_code": status_code,
            "elapsed_ms": _elapsed_ms(started_at),
        },
    )


def _log_http_error(request: EnjiHttpRequest, started_at: float, exc: httpx.HTTPError) -> None:
    log_event(
        _LOGGER,
        logging.WARNING,
        "enji_http_error",
        {
            "operation": request.operation,
            "method": request.method,
            "path": _url_path(request.url),
            "error_type": exc.__class__.__name__,
            "elapsed_ms": _elapsed_ms(started_at),
        },
    )


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
