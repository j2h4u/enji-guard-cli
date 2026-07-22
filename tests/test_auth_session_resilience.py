import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

import enji_guard_cli.auth_session.api as auth_module
import enji_guard_cli.auth_session.auto_refresh as auto_refresh_module
import enji_guard_cli.enji_gateway.client as api_client_module
from enji_guard_cli.auth_session.adapters import AuthSessionAdapter
from enji_guard_cli.auth_session.api import (
    backend_readiness_probe_async,
    import_cookie,
    load_stored_auth,
    refresh_cookie_auth,
)
from enji_guard_cli.auth_session.store import (
    load_pending_rotation,
    mark_pending_rotation_rotated,
    pending_rotation_path,
    reserve_pending_rotation,
)
from enji_guard_cli.enji_gateway.client import (
    ApiEndpoint,
    ApiRequestSpec,
    EnjiApiSession,
    request_json_object,
)
from enji_guard_cli.enji_gateway.contract import (
    IMPLEMENTED_ENJI_ENDPOINTS,
    RetryProfile,
)
from enji_guard_cli.settings import AutoRefreshSettings
from enji_guard_cli.transport import EnjiHttpRequest, EnjiHttpResponse, HttpxEnjiHttpClient, RetryConfig


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
        await refresh_cookie_auth(auth_file, stored_auth, Client())
        await backend_readiness_probe_async(auth_file, Client())

    asyncio.run(run())
    assert captured == [RetryProfile.AUTH_REFRESH, RetryProfile.READ]
    assert not pending_rotation_path(auth_file).exists()


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


def test_unsafe_request_is_not_replayed_after_cookie_refresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    session = api_client_module.load_api_session(auth_file, auth_port=AuthSessionAdapter())
    calls: list[EnjiHttpRequest] = []

    class Client:
        async def request(self, request: EnjiHttpRequest):
            calls.append(request)
            return api_client_module.EnjiHttpResponse(
                status_code=401,
                headers={},
                content=b'{"error":{"code":"AUTH_INVALID"}}',
            )

    async def fake_refresh(current: EnjiApiSession, _client: object) -> None:
        current.update_stored_auth(current.stored_auth)

    monkeypatch.setattr(api_client_module, "refresh_session", fake_refresh)

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


def test_pending_rotated_cookie_journal_recovers_from_disk_and_is_private(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None

    pending = reserve_pending_rotation(auth_file, stored_auth)
    journal_path = pending_rotation_path(auth_file)
    assert journal_path.stat().st_mode & 0o777 == 0o600
    mark_pending_rotation_rotated(auth_file, pending, "access=new; refresh=rotated")

    # A fresh load is the process-restart boundary: recovery must use the
    # durable rotated value and must not issue another refresh request.
    reloaded_pending = load_pending_rotation(auth_file)
    assert reloaded_pending is not None
    assert reloaded_pending["replacement_cookie_header"] == "access=new; refresh=rotated"

    calls = 0

    class Client:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            nonlocal calls
            calls += 1
            raise AssertionError("durable rotated journal should avoid refresh request")

    recovered = asyncio.run(refresh_cookie_auth(auth_file, stored_auth, Client()))

    assert calls == 0
    assert recovered["credential"] == {"type": "cookie", "cookie_header": "access=new; refresh=rotated"}
    assert not journal_path.exists()


def test_refresh_reserve_storage_failure_avoids_refresh_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None

    def fail_reserve(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "disk full")

    monkeypatch.setattr(auth_module, "reserve_pending_rotation", fail_reserve)
    calls = 0

    class Client:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            nonlocal calls
            calls += 1
            raise AssertionError("refresh request must not run without a journal")

    with pytest.raises(auth_module.EnjiHttpError, match="failed to reserve refreshed cookie"):
        asyncio.run(refresh_cookie_auth(auth_file, stored_auth, Client()))
    assert calls == 0


def test_requested_rotation_never_replays_after_request_crash(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=one-time", auth_file)
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None

    first_calls = 0

    class CrashedClient:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            nonlocal first_calls
            first_calls += 1
            raise OSError("process crashed after sending refresh request")

    with pytest.raises(OSError, match="process crashed"):
        asyncio.run(refresh_cookie_auth(auth_file, stored_auth, CrashedClient()))
    pending = load_pending_rotation(auth_file)
    assert pending is not None
    assert pending["state"] == "requested"
    assert first_calls == 1

    second_calls = 0

    class MustNotReplayClient:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            nonlocal second_calls
            second_calls += 1
            raise AssertionError("a requested one-time refresh must not be replayed")

    with pytest.raises(auth_module.EnjiHttpError, match="outcome is unknown") as exc_info:
        asyncio.run(refresh_cookie_auth(auth_file, stored_auth, MustNotReplayClient()))
    assert exc_info.value.code == "STORAGE"
    assert second_calls == 0
    assert load_pending_rotation(auth_file) is not None


def test_legacy_reserved_rotation_is_treated_as_unknown(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=one-time", auth_file)
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    pending_rotation_path(auth_file).write_text(
        json.dumps(
            {
                "version": 1,
                "state": "reserved",
                "previous_auth": stored_auth,
                "replacement_cookie_header": None,
                "error_type": None,
                "errno": None,
            }
        ),
        encoding="utf-8",
    )

    class MustNotReplayClient:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            raise AssertionError("an ambiguous legacy reservation must not be replayed")

    with pytest.raises(auth_module.EnjiHttpError, match="outcome is unknown"):
        asyncio.run(refresh_cookie_auth(auth_file, stored_auth, MustNotReplayClient()))
    pending = load_pending_rotation(auth_file)
    assert pending is not None
    assert pending["state"] == "requested"


def test_invalid_rotation_journal_fails_closed_without_refresh_request(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=one-time", auth_file)
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    pending_rotation_path(auth_file).write_text("not-json", encoding="utf-8")

    class MustNotReplayClient:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            raise AssertionError("an invalid journal must block refresh")

    with pytest.raises(auth_module.EnjiHttpError, match="journal is invalid") as exc_info:
        asyncio.run(refresh_cookie_auth(auth_file, stored_auth, MustNotReplayClient()))
    assert exc_info.value.code == "STORAGE"


def test_rejected_refresh_keeps_no_replay_fence(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=one-time", auth_file)
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None

    class Client:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            return EnjiHttpResponse(status_code=401, headers={}, content=b"{}")

    with pytest.raises(auth_module.EnjiHttpError) as exc_info:
        asyncio.run(refresh_cookie_auth(auth_file, stored_auth, Client()))
    assert exc_info.value.code == "AUTH_REQUIRED"
    pending = load_pending_rotation(auth_file)
    assert pending is not None
    assert pending["state"] == "requested"


def test_auto_refresh_backoff_grows_caps_and_auth_required_is_calmer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auto_refresh_module.random, "uniform", lambda _low, _high: 0.0)
    settings = AutoRefreshSettings(
        enabled=True,
        lead_seconds=300,
        fallback_seconds=900,
        retry_seconds=900,
        retry_initial_seconds=30,
        retry_max_seconds=10_000,
        retry_jitter_seconds=30,
        auth_required_retry_seconds=900,
    )
    wait = auto_refresh_module._AuthRefreshWait(settings)

    def delay(attempt: int, exception: object) -> float:
        state = SimpleNamespace(
            attempt_number=attempt,
            outcome=SimpleNamespace(exception=lambda: exception),
        )
        return wait(cast(auto_refresh_module.RetryCallState, state))

    assert [delay(attempt, RuntimeError()) for attempt in range(1, 5)] == [30, 60, 120, 240]
    assert delay(20, RuntimeError()) == 10_000
    assert [delay(attempt, SimpleNamespace(code="AUTH_REQUIRED")) for attempt in range(1, 4)] == [900, 900, 900]
