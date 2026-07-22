import asyncio
from pathlib import Path

import httpx
import pytest

import enji_guard_cli.enji_gateway.client as api_client_module
from enji_guard_cli.auth_session.adapters import GatewayCredentialReader
from enji_guard_cli.auth_session.api import (
    _refresh_cookie_auth,
    backend_readiness_probe_async,
    import_cookie,
    load_stored_auth,
)
from enji_guard_cli.auth_session.state_machine import Rotated
from enji_guard_cli.auth_session.store import (
    pending_rotation_path,
    write_journal,
)
from enji_guard_cli.enji_gateway.client import (
    ApiEndpoint,
    ApiRequestSpec,
    request_json_object,
)
from enji_guard_cli.enji_gateway.contract import (
    IMPLEMENTED_ENJI_ENDPOINTS,
    RetryProfile,
)
from enji_guard_cli.transport import EnjiHttpError, EnjiHttpRequest, EnjiHttpResponse, HttpxEnjiHttpClient, RetryConfig


def test_api_endpoint_request_preserves_retry_profile_for_every_implemented_path() -> None:
    for endpoint in IMPLEMENTED_ENJI_ENDPOINTS:
        api_endpoint = ApiEndpoint(endpoint, lambda payload: payload)
        assert api_endpoint.request().retry_profile is endpoint.retry_profile


def test_auth_request_paths_assign_auth_refresh_and_read_profiles(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    captured: list[RetryProfile] = []

    class Client:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            captured.append(request.profile)
            if request.profile is RetryProfile.AUTH_REFRESH:
                return EnjiHttpResponse(
                    status_code=200,
                    headers={},
                    content=b'{"ok":true}',
                    set_cookie_headers=("access_token=new", "refresh_token=rotated"),
                )
            return EnjiHttpResponse(
                status_code=200,
                headers={},
                content=b'{"email":"user@example.com","name":"User","user_id":"u1"}',
            )

    async def run() -> None:
        stored_auth = load_stored_auth(auth_file)
        assert stored_auth is not None
        await _refresh_cookie_auth(auth_file, stored_auth, Client())
        await backend_readiness_probe_async(auth_file, Client())

    asyncio.run(run())
    assert captured == [RetryProfile.AUTH_REFRESH, RetryProfile.READ]


@pytest.mark.parametrize("profile", [RetryProfile.READ, RetryProfile.IDEMPOTENT_MUTATION])
@pytest.mark.parametrize("status,headers", [(503, {}), (429, {"Retry-After": "0"})])
def test_retryable_profiles_retry_5xx_and_rate_limit_responses(
    profile: RetryProfile, status: int, headers: dict[str, str]
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        response_status = status if calls == 1 else 200
        return httpx.Response(response_status, headers=headers, json={"ok": True}, request=request)

    async def run() -> None:
        config = RetryConfig(
            total=1,
            backoff_factor=0,
            max_delay_seconds=1,
            jitter_seconds=0,
            respect_retry_after_header=True,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw_client:
            client = HttpxEnjiHttpClient(raw_client, retry_config=config)
            response = await client.request(
                EnjiHttpRequest(
                    method="GET",
                    url="https://fleet.enji.ai/api/ux/catalog",
                    operation="catalog",
                    headers={},
                    profile=profile,
                )
            )
            assert response.status_code == 200

    asyncio.run(run())
    assert calls == 2


@pytest.mark.parametrize("profile", [RetryProfile.UNSAFE_MUTATION, RetryProfile.AUTH_REFRESH])
def test_non_replayable_profiles_do_not_auto_retry_status_responses(profile: RetryProfile) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, headers={"Retry-After": "0"}, request=request)

    async def run() -> None:
        config = RetryConfig(total=3, backoff_factor=0, jitter_seconds=0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw_client:
            response = await HttpxEnjiHttpClient(raw_client, retry_config=config).request(
                EnjiHttpRequest(
                    method="POST",
                    url="https://fleet.enji.ai/api/v1/auth/refresh",
                    operation="auth refresh",
                    headers={},
                    profile=profile,
                )
            )
            assert response.status_code == 503

    asyncio.run(run())
    assert calls == 1


def test_gateway_unsafe_request_is_not_replayed_or_refreshed(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    session = api_client_module.load_api_session(auth_file, auth_port=GatewayCredentialReader())
    calls: list[EnjiHttpRequest] = []

    class Client:
        async def request(self, request: EnjiHttpRequest):
            calls.append(request)
            return api_client_module.EnjiHttpResponse(
                status_code=401,
                headers={},
                content=b'{"error":{"code":"AUTH_INVALID"}}',
            )

    spec = ApiRequestSpec(
        method="POST",
        path="/api/ux/projects",
        operation="project create",
        parser=lambda payload: payload,
        retry_profile=RetryProfile.UNSAFE_MUTATION,
        json_body={"name": "demo"},
    )

    with pytest.raises(api_client_module.EnjiHttpError, match="invalid access token"):
        asyncio.run(request_json_object(session, Client(), spec))

    assert len(calls) == 1
    assert calls[0].profile is RetryProfile.UNSAFE_MUTATION


def test_rotated_cookie_journal_recovers_from_disk_without_replaying_post(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None

    write_journal(auth_file, Rotated(stored_auth["revision"], "access=new; refresh=rotated"))
    journal_path = pending_rotation_path(auth_file)
    assert journal_path.stat().st_mode & 0o777 == 0o600

    calls = 0

    class Client:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            nonlocal calls
            calls += 1
            raise AssertionError("durable rotated journal should avoid refresh request")

    recovered = asyncio.run(_refresh_cookie_auth(auth_file, stored_auth, Client()))

    assert calls == 0
    assert recovered["credential"] == {"type": "cookie", "cookie_header": "access=new; refresh=rotated"}
    # An unacknowledged durable outcome remains in the outbox for the runtime
    # telemetry sink to drain on a later reconciliation.
    assert journal_path.exists()


def test_corrupt_refresh_journal_avoids_refresh_request(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None

    pending_rotation_path(auth_file).write_text("not json", encoding="utf-8")
    calls = 0

    class Client:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            nonlocal calls
            calls += 1
            raise AssertionError("refresh request must not run without a journal")

    with pytest.raises(EnjiHttpError, match="refresh journal is corrupt"):
        asyncio.run(_refresh_cookie_auth(auth_file, stored_auth, Client()))
    assert calls == 0
