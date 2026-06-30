import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import cast

import httpx
import pytest

from enji_guard_cli.settings import TelemetrySettings
from enji_guard_cli.telemetry import configure_logging
from enji_guard_cli.transport import (
    EnjiHttpRequest,
    EnjiHttpResponse,
    EnjiRateLimitError,
    EnjiResponseDecodeError,
    EnjiTransportError,
    HttpxEnjiHttpClient,
    RetryConfig,
    raise_for_response_status,
    retry_after_seconds,
)


def test_httpx_enji_http_client_returns_response_body() -> None:
    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}, request=request))
        ) as client:
            response = await HttpxEnjiHttpClient(client).request(
                EnjiHttpRequest(
                    method="GET",
                    url="https://fleet.enji.ai/api/v1/auth/me",
                    operation="auth status",
                    headers={"Authorization": "Bearer token-123"},
                )
            )

        assert response.status_code == 200
        assert response.json(operation="auth status") == {"ok": True}

    run_async(run)


def test_httpx_enji_http_client_logs_sanitized_request_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(
        TelemetrySettings(
            level_name="INFO",
            log_format="json",
            log_file=None,
            max_bytes=10_000,
            backup_count=1,
        )
    )

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}, request=request))
        ) as client:
            await HttpxEnjiHttpClient(client).request(
                EnjiHttpRequest(
                    method="GET",
                    url="https://fleet.enji.ai/api/ux/me/access?token=secret",
                    operation="access",
                    headers={"Cookie": "session=secret"},
                )
            )

    run_async(run)

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = cast(object, json.loads(captured.err))
    assert isinstance(payload, dict)
    assert payload["message"] == "enji_http_response"
    assert payload["operation"] == "access"
    assert payload["method"] == "GET"
    assert payload["path"] == "/api/ux/me/access"
    assert payload["status_code"] == 200
    assert "secret" not in json.dumps(payload)


def test_httpx_enji_http_client_wraps_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                await HttpxEnjiHttpClient(client).request(
                    EnjiHttpRequest(
                        method="GET",
                        url="https://fleet.enji.ai/api/v1/auth/me",
                        operation="auth status",
                        headers={},
                    )
                )
            except EnjiTransportError as exc:
                assert exc.code == "UPSTREAM"
                assert exc.message == "auth status request failed: boom"
            else:
                raise AssertionError("expected EnjiTransportError")

    run_async(run)


def test_raise_for_response_status_returns_retry_after_seconds() -> None:
    future_value = format_datetime(datetime.now(UTC) + timedelta(seconds=11), usegmt=True)
    response = httpx.Response(429, headers={"Retry-After": future_value})

    try:
        raise_for_response_status(
            response=_response_from_httpx(response),
            operation="auth status",
            expected_statuses={200, 401, 403},
        )
    except EnjiRateLimitError as exc:
        assert exc.code == "RATE_LIMIT"
        assert exc.retry_after_seconds is not None
        assert 0 <= exc.retry_after_seconds <= 11
    else:
        raise AssertionError("expected EnjiRateLimitError")


def test_response_json_raises_decode_error_for_invalid_json() -> None:
    response = _response_from_httpx(httpx.Response(200, text="not-json"))

    try:
        response.json(operation="auth status")
    except EnjiResponseDecodeError as exc:
        assert exc.code == "UPSTREAM"
        assert exc.message == "auth status returned invalid JSON"
    else:
        raise AssertionError("expected EnjiResponseDecodeError")


def test_retry_after_seconds_accepts_delta_seconds() -> None:
    assert retry_after_seconds({"Retry-After": "9"}) == 9


def test_retry_config_build_defaults_to_no_retries() -> None:
    retry = RetryConfig().build()

    assert retry.total == 0


def _response_from_httpx(response: httpx.Response) -> EnjiHttpResponse:
    return EnjiHttpResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        content=response.content,
    )


def run_async(task_factory: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(task_factory())
