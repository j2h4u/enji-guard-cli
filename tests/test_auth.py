import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NotRequired, TypedDict, TypeGuard, cast

import httpx
import pytest
from typer.testing import CliRunner

import enji_guard_cli.auth as auth_module
import enji_guard_cli.auth_impl.auto_refresh as auto_refresh_module
from enji_guard_cli.auth import (
    AUTH_REFRESH_USER_AGENT,
    AuthError,
    AuthRefreshPayload,
    AuthStatusPayload,
    auth_headers,
    auth_status_async,
    backend_readiness_probe_async,
    cookie_access_expires_at,
    cookie_refresh_sleep_seconds,
    import_bearer_token,
    import_cookie,
    load_stored_auth,
    merge_set_cookie_headers,
    refresh_auth_async,
    set_cookie_names,
    start_auto_refresh_task,
)
from enji_guard_cli.auth import StoredAuth as RuntimeStoredAuth
from enji_guard_cli.cli import app
from enji_guard_cli.readiness import BackendReadinessProbe
from enji_guard_cli.settings import DEFAULT_GUARD_ORIGIN, DEFAULT_GUARD_REFERER, AutoRefreshSettings
from enji_guard_cli.transport import EnjiHttpRequest, EnjiHttpResponse, HttpxEnjiHttpClient

AUTH_REFRESH_ORIGIN = DEFAULT_GUARD_ORIGIN
AUTH_REFRESH_REFERER = DEFAULT_GUARD_REFERER


class ImportPayload(TypedDict):
    ok: bool
    auth_file: str
    credential_type: str
    cookie_count: NotRequired[int]


class StoredCredential(TypedDict, total=False):
    type: str
    cookie_header: str
    token: str


class StoredAuth(TypedDict):
    version: int
    base_url: str
    credential: StoredCredential
    imported_at: str


def test_import_cookie_stores_cookie_credential(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    result = import_cookie("Cookie: session=abc; refresh=def", auth_file)

    stored = cast(StoredAuth, json.loads(auth_file.read_text(encoding="utf-8")))
    assert result["credential_type"] == "cookie"
    assert result.get("cookie_count") == 2
    assert stored["credential"] == {
        "type": "cookie",
        "cookie_header": "session=abc; refresh=def",
    }
    assert auth_file.stat().st_mode & 0o777 == 0o600


def test_import_bearer_token_stores_token_credential(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    result = import_bearer_token("Authorization: Bearer token-123\n", auth_file)

    stored = cast(StoredAuth, json.loads(auth_file.read_text(encoding="utf-8")))
    assert result["credential_type"] == "bearer_token"
    assert stored["credential"] == {"type": "bearer_token", "token": "token-123"}
    assert auth_headers(cast(RuntimeStoredAuth, stored)) == {"Authorization": "Bearer token-123"}


def test_auto_refresh_loop_retries_after_storage_or_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    slept_with: list[int] = []

    class FakeHttpxEnjiHttpClient:
        async def __aenter__(self) -> FakeHttpxEnjiHttpClient:
            return self

        async def __aexit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
            return None

        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            raise AssertionError(f"request should not be called: {request.operation}")

    def fake_sleep_seconds(*, auth_file: Path, refresh_settings: AutoRefreshSettings, **_kwargs: object) -> int:
        raise ValueError("invalid auth state")

    async def fake_sleep(seconds: int) -> None:
        slept_with.append(seconds)
        raise asyncio.CancelledError

    def fake_log_event(*_args: object, **_kwargs: object) -> None:
        return None

    async def fail_refresh(*_args: object, **_kwargs: object) -> RuntimeStoredAuth:
        raise AssertionError("refresh should not be called")

    def is_refresh_error(exc: Exception) -> TypeGuard[auto_refresh_module.RefreshErrorLike]:
        return False

    def cookie_expires(*_args: object, **_kwargs: object) -> datetime | None:
        return None

    monkeypatch.setattr(auto_refresh_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            auto_refresh_module._auto_refresh_loop(
                auth_file=Path("auth.json"),
                refresh_settings=AutoRefreshSettings(
                    enabled=True,
                    lead_seconds=300,
                    fallback_seconds=900,
                    retry_seconds=60,
                ),
                dependencies=auto_refresh_module.AutoRefreshLoopDependencies(
                    sleep_seconds_fn=fake_sleep_seconds,
                    load_sleep_seconds_stored_auth_fn=lambda _path: None,
                    cookie_refresh_sleep_seconds_fn=lambda *_args, **_kwargs: 0,
                    refresh_stored_cookie_auth_fn=fail_refresh,
                    cookie_access_expires_at_fn=cookie_expires,
                    is_refresh_error_fn=is_refresh_error,
                    log_event_fn=fake_log_event,
                    logger=auto_refresh_module.logging.getLogger("test"),
                    sleep_fn=fake_sleep,
                    client_factory=FakeHttpxEnjiHttpClient,
                ),
            )
        )

    assert slept_with == [60]


def test_merge_set_cookie_headers_updates_existing_cookie_without_keeping_attributes() -> None:
    updated = merge_set_cookie_headers(
        "access=old; refresh=long",
        ["access=new; Path=/; HttpOnly; SameSite=Lax"],
    )

    assert updated.value == "access=new; refresh=long"
    assert updated.count == 2


def test_set_cookie_names_returns_only_cookie_names() -> None:
    assert set_cookie_names(
        [
            "access_token=secret; Path=/; HttpOnly",
            "refresh_token=also-secret; Path=/api/v1/auth; HttpOnly",
        ]
    ) == ("access_token", "refresh_token")


def test_cookie_access_expires_at_reads_access_token_jwt_expiration(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    expires_at = datetime(2026, 6, 29, 12, 15, tzinfo=UTC)
    import_cookie(f"access_token={unsigned_jwt({'exp': int(expires_at.timestamp())})}; refresh_token=long", auth_file)
    stored_auth = load_stored_auth(auth_file)

    assert stored_auth is not None
    assert cookie_access_expires_at(stored_auth) == expires_at


def test_cookie_refresh_sleep_seconds_refreshes_before_access_expiration(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    expires_at = now + timedelta(minutes=12)
    import_cookie(f"access_token={unsigned_jwt({'exp': int(expires_at.timestamp())})}; refresh_token=long", auth_file)
    stored_auth = load_stored_auth(auth_file)

    assert stored_auth is not None
    assert cookie_refresh_sleep_seconds(stored_auth, now, settings=auto_refresh_settings()) == 420


def test_cookie_refresh_sleep_seconds_returns_zero_inside_refresh_window(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    expires_at = now + timedelta(minutes=3)
    import_cookie(f"access_token={unsigned_jwt({'exp': int(expires_at.timestamp())})}; refresh_token=long", auth_file)
    stored_auth = load_stored_auth(auth_file)

    assert stored_auth is not None
    assert cookie_refresh_sleep_seconds(stored_auth, now, settings=auto_refresh_settings()) == 0


def test_import_locks_down_existing_parent_directory(tmp_path: Path) -> None:
    auth_dir = tmp_path / "existing"
    auth_dir.mkdir()
    auth_dir.chmod(0o755)

    import_bearer_token("token-123", auth_dir / "auth.json")

    assert auth_dir.stat().st_mode & 0o777 == 0o700


def test_auth_status_uses_stored_credential_headers(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    recorded_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_headers.update(dict(request.headers))
        return httpx.Response(
            200,
            json={"email": "user@example.com", "name": "User", "user_id": "user_1"},
            request=request,
        )

    async def run_status() -> AuthStatusPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await auth_status_async(auth_file, HttpxEnjiHttpClient(client))

    status = run_auth_status(run_status)

    assert status["authenticated"] is True
    assert status["credential_type"] == "bearer_token"
    assert status["email"] == "user@example.com"
    assert recorded_headers["authorization"] == "Bearer token-123"


def test_auth_status_returns_rate_limit_payload(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("session=abc", auth_file)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"}, request=request)

    async def run_status() -> AuthStatusPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await auth_status_async(auth_file, HttpxEnjiHttpClient(client))

    status = run_auth_status(run_status)

    assert status["authenticated"] is False
    assert status["credential_type"] == "cookie"
    assert status["code"] == "RATE_LIMIT"
    assert status["message"] == "auth status was rate limited; retry after 7s"


def test_auth_status_refreshes_cookie_on_auth_invalid(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    captured: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path, request.headers.get("cookie")))
        if len(captured) == 1:
            return httpx.Response(401, json={"error": {"code": "AUTH_INVALID"}}, request=request)
        if len(captured) == 2:
            assert request.headers["origin"] == AUTH_REFRESH_ORIGIN
            assert request.headers["referer"] == AUTH_REFRESH_REFERER
            assert request.headers["user-agent"] == AUTH_REFRESH_USER_AGENT
            return httpx.Response(
                200,
                json={"message": "token refreshed"},
                headers=[
                    ("Set-Cookie", "access_token=new; Path=/; HttpOnly"),
                    ("Set-Cookie", "refresh_token=new-refresh; Path=/api/v1/auth; HttpOnly"),
                ],
                request=request,
            )
        return httpx.Response(
            200,
            json={"email": "user@example.com", "name": "User", "user_id": "user_1"},
            request=request,
        )

    async def run_status() -> AuthStatusPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await auth_status_async(auth_file, HttpxEnjiHttpClient(client))

    status = run_auth_status(run_status)

    assert status["authenticated"] is True
    assert status["email"] == "user@example.com"
    assert captured == [
        ("GET", "/api/v1/auth/me", "access_token=old; refresh_token=long"),
        ("POST", "/api/v1/auth/refresh", "access_token=old; refresh_token=long"),
        ("GET", "/api/v1/auth/me", "access_token=new; refresh_token=new-refresh"),
    ]
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {
        "type": "cookie",
        "cookie_header": "access_token=new; refresh_token=new-refresh",
    }


def test_backend_readiness_probe_does_not_refresh_on_auth_invalid(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path))
        return httpx.Response(401, json={"error": {"code": "AUTH_INVALID"}}, request=request)

    async def run_probe() -> BackendReadinessProbe:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await backend_readiness_probe_async(auth_file, HttpxEnjiHttpClient(client))

    probe = asyncio.run(run_probe())

    assert probe.ready is False
    assert probe.failure_kind == "auth"
    assert probe.failure_code == "AUTH_INVALID"
    assert probe.credential_type == "cookie"
    assert captured == [("GET", "/api/v1/auth/me")]
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access=old; refresh=long"}


def test_auth_status_reports_auth_required_when_refresh_cookie_is_invalid(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=expired", auth_file)
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path))
        if len(captured) == 1:
            return httpx.Response(401, json={"error": {"code": "AUTH_INVALID"}}, request=request)
        return httpx.Response(401, json={"error": {"code": "AUTH_REQUIRED"}}, request=request)

    async def run_status() -> AuthStatusPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await auth_status_async(auth_file, HttpxEnjiHttpClient(client))

    status = run_auth_status(run_status)

    assert status["authenticated"] is False
    assert status["code"] == "AUTH_REQUIRED"
    assert status["message"] == "stored refresh cookie is not authenticated"
    assert captured == [("GET", "/api/v1/auth/me"), ("POST", "/api/v1/auth/refresh")]


def test_auth_status_preserves_rate_limit_after_refresh(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path))
        if len(captured) == 1:
            return httpx.Response(401, json={"error": {"code": "AUTH_INVALID"}}, request=request)
        if len(captured) == 2:
            return httpx.Response(
                200,
                json={"message": "token refreshed"},
                headers=[
                    ("Set-Cookie", "access_token=new; Path=/; HttpOnly"),
                    ("Set-Cookie", "refresh_token=new-refresh; Path=/api/v1/auth; HttpOnly"),
                ],
                request=request,
            )
        return httpx.Response(429, headers={"Retry-After": "7"}, request=request)

    async def run_status() -> AuthStatusPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await auth_status_async(auth_file, HttpxEnjiHttpClient(client))

    status = run_auth_status(run_status)

    assert status["authenticated"] is False
    assert status["code"] == "RATE_LIMIT"
    assert status["message"] == "auth status was rate limited; retry after 7s"


def test_auth_status_reports_repeated_auth_invalid_after_refresh(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path))
        if len(captured) == 2:
            return httpx.Response(
                200,
                json={"message": "token refreshed"},
                headers=[
                    ("Set-Cookie", "access_token=new; Path=/; HttpOnly"),
                    ("Set-Cookie", "refresh_token=new-refresh; Path=/api/v1/auth; HttpOnly"),
                ],
                request=request,
            )
        return httpx.Response(401, json={"error": {"code": "AUTH_INVALID"}}, request=request)

    async def run_status() -> AuthStatusPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await auth_status_async(auth_file, HttpxEnjiHttpClient(client))

    status = run_auth_status(run_status)

    assert status["authenticated"] is False
    assert status["code"] == "AUTH_INVALID"
    assert status["message"] == "invalid access token after refresh"


def test_refresh_auth_updates_rotated_access_and_refresh_cookies(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    expires_at = datetime(2026, 6, 29, 12, 15, tzinfo=UTC)
    import_cookie("access_token=old; refresh_token=old", auth_file)
    captured: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path, request.headers.get("cookie")))
        return httpx.Response(
            200,
            json={"message": "token refreshed"},
            headers=[
                ("Set-Cookie", f"access_token={unsigned_jwt({'exp': int(expires_at.timestamp())})}; Path=/; HttpOnly"),
                ("Set-Cookie", "refresh_token=new; Path=/api/v1/auth; HttpOnly"),
            ],
            request=request,
        )

    async def run_refresh() -> AuthRefreshPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_auth_async(auth_file, HttpxEnjiHttpClient(client))

    payload = asyncio.run(run_refresh())

    assert payload["ok"] is True
    assert payload["credential_type"] == "cookie"
    assert payload["cookie_count"] == 2
    assert payload["access_expires_at"] == expires_at.isoformat()
    assert captured == [("POST", "/api/v1/auth/refresh", "access_token=old; refresh_token=old")]
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {
        "type": "cookie",
        "cookie_header": f"access_token={unsigned_jwt({'exp': int(expires_at.timestamp())})}; refresh_token=new",
    }


def test_refresh_auth_recovers_deferred_cookie_rotation_without_second_post(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_file = tmp_path / "auth.json"
    access_token = unsigned_jwt({"exp": 1782744000})
    import_cookie("access_token=old; refresh_token=old", auth_file)
    post_count = 0
    persistence_attempts = 0
    original_replace = auth_module.replace_cookie_credential

    def fail_twice(path: Path, stored: RuntimeStoredAuth, cookie_header: str) -> RuntimeStoredAuth:
        nonlocal persistence_attempts
        persistence_attempts += 1
        if persistence_attempts <= 2:
            raise OSError(28, "disk full")
        return original_replace(path, stored, cookie_header)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        post_count += 1
        return httpx.Response(
            200,
            headers=[
                ("Set-Cookie", f"access_token={access_token}; Path=/; HttpOnly"),
                ("Set-Cookie", "refresh_token=new; Path=/api/v1/auth; HttpOnly"),
            ],
            request=request,
        )

    monkeypatch.setattr(auth_module, "replace_cookie_credential", fail_twice)

    async def run_refresh() -> AuthRefreshPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_auth_async(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(AuthError) as exc_info:
        asyncio.run(run_refresh())
    assert exc_info.value.code == "STORAGE"
    with pytest.raises(AuthError) as exc_info:
        asyncio.run(run_refresh())
    assert exc_info.value.code == "STORAGE"

    payload = asyncio.run(run_refresh())

    assert payload["ok"] is True
    assert post_count == 1
    assert persistence_attempts == 3
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {
        "type": "cookie",
        "cookie_header": f"access_token={access_token}; refresh_token=new",
    }


def test_refresh_auth_accepts_already_persisted_deferred_rotation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_file = tmp_path / "auth.json"
    access_token = unsigned_jwt({"exp": 1782744000})
    import_cookie("access_token=old; refresh_token=old", auth_file)
    original_replace = auth_module.replace_cookie_credential
    post_count = 0

    def fail_persistence(_path: Path, _stored: RuntimeStoredAuth, _cookie_header: str) -> RuntimeStoredAuth:
        raise OSError(28, "disk full")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        post_count += 1
        return httpx.Response(
            200,
            headers=[
                ("Set-Cookie", f"access_token={access_token}; Path=/; HttpOnly"),
                ("Set-Cookie", "refresh_token=rotated; Path=/api/v1/auth; HttpOnly"),
            ],
            request=request,
        )

    monkeypatch.setattr(auth_module, "replace_cookie_credential", fail_persistence)

    async def run_refresh() -> AuthRefreshPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_auth_async(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(AuthError):
        asyncio.run(run_refresh())

    initial_auth = load_stored_auth(auth_file)
    assert initial_auth is not None
    original_replace(auth_file, initial_auth, f"access_token={access_token}; refresh_token=rotated")

    payload = asyncio.run(run_refresh())

    assert payload["ok"] is True
    assert post_count == 1


def test_refresh_auth_discards_deferred_rotation_when_auth_file_is_superseded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    original_replace = auth_module.replace_cookie_credential
    fail_persistence = True

    def fail_then_write(path: Path, stored: RuntimeStoredAuth, cookie_header: str) -> RuntimeStoredAuth:
        if fail_persistence:
            raise OSError(5, "io error")
        return original_replace(path, stored, cookie_header)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=[
                ("Set-Cookie", f"access_token={unsigned_jwt({'exp': 1782744000})}; Path=/; HttpOnly"),
                ("Set-Cookie", "refresh_token=rotated; Path=/api/v1/auth; HttpOnly"),
            ],
            request=request,
        )

    monkeypatch.setattr(auth_module, "replace_cookie_credential", fail_then_write)

    async def run_refresh() -> AuthRefreshPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_auth_async(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(AuthError):
        asyncio.run(run_refresh())

    import_cookie("access_token=operator; refresh_token=operator", auth_file)
    payload = asyncio.run(run_refresh())

    assert payload["ok"] is True
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {
        "type": "cookie",
        "cookie_header": "access_token=operator; refresh_token=operator",
    }


def test_refresh_auth_rejects_success_response_without_refresh_cookie(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": "token refreshed"},
            headers=[("Set-Cookie", f"access_token={unsigned_jwt({'exp': 1782744000})}; Path=/; HttpOnly")],
            request=request,
        )

    async def run_refresh() -> AuthRefreshPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_auth_async(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(AuthError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "UPSTREAM"
    assert exc_info.value.message == "auth refresh did not return refresh_token Set-Cookie"
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access_token=old; refresh_token=old"}


def test_refresh_auth_persists_rotated_cookies_before_transient_error(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    expires_at = datetime(2026, 6, 29, 12, 15, tzinfo=UTC)
    access_token = unsigned_jwt({"exp": int(expires_at.timestamp())})
    import_cookie("access_token=old; refresh_token=old", auth_file)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            headers=[
                ("Set-Cookie", f"access_token={access_token}; Path=/; HttpOnly"),
                ("Set-Cookie", "refresh_token=new; Path=/api/v1/auth; HttpOnly"),
            ],
            request=request,
        )

    async def run_refresh() -> AuthRefreshPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_auth_async(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(AuthError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "UPSTREAM"
    assert exc_info.value.message == "auth refresh failed with HTTP 502"
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {
        "type": "cookie",
        "cookie_header": f"access_token={access_token}; refresh_token=new",
    }


def test_refresh_auth_does_not_persist_incomplete_transient_cookie_rotation(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            headers=[("Set-Cookie", f"access_token={unsigned_jwt({'exp': 1782744000})}; Path=/; HttpOnly")],
            request=request,
        )

    async def run_refresh() -> AuthRefreshPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_auth_async(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(AuthError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "UPSTREAM"
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access_token=old; refresh_token=old"}


def test_refresh_auth_does_not_persist_deleting_auth_cookie_from_transient_error(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            headers=[
                ("Set-Cookie", f"access_token={unsigned_jwt({'exp': 1782744000})}; Path=/; HttpOnly"),
                ("Set-Cookie", "refresh_token=; Path=/api/v1/auth; Max-Age=0; HttpOnly"),
            ],
            request=request,
        )

    async def run_refresh() -> AuthRefreshPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_auth_async(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(AuthError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "UPSTREAM"
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access_token=old; refresh_token=old"}


def test_refresh_auth_rejects_deleting_auth_cookie_from_success_response(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": "token refreshed"},
            headers=[
                ("Set-Cookie", f"access_token={unsigned_jwt({'exp': 1782744000})}; Path=/; HttpOnly"),
                ("Set-Cookie", "refresh_token=; Path=/api/v1/auth; Max-Age=0; HttpOnly"),
            ],
            request=request,
        )

    async def run_refresh() -> AuthRefreshPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_auth_async(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(AuthError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "AUTH_REQUIRED"
    assert exc_info.value.message == "auth refresh returned non-persistable refresh_token cookie"
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access_token=old; refresh_token=old"}


def test_refresh_auth_does_not_persist_cookies_from_auth_failure(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            headers=[
                ("Set-Cookie", f"access_token={unsigned_jwt({'exp': 1782744000})}; Path=/; HttpOnly"),
                ("Set-Cookie", "refresh_token=rejected; Path=/api/v1/auth; HttpOnly"),
            ],
            request=request,
        )

    async def run_refresh() -> AuthRefreshPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_auth_async(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(AuthError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "AUTH_REQUIRED"
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access_token=old; refresh_token=old"}


def test_start_auto_refresh_task_skips_bearer_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)

    monkeypatch.setattr(
        "enji_guard_cli.auth.default_settings",
        lambda: type(
            "Settings",
            (),
            {
                "auto_refresh": auto_refresh_settings(),
                "auth": type("Auth", (), {"auth_file": auth_file})(),
            },
        )(),
    )

    assert start_auto_refresh_task() is None


def test_start_auto_refresh_task_runs_without_bootstrapped_auth_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_file = tmp_path / "auth.json"
    captured: dict[str, object] = {}

    async def fake_auto_refresh_loop(
        *, auth_file: Path, refresh_settings: AutoRefreshSettings, **_kwargs: object
    ) -> None:
        captured["auth_file"] = auth_file
        captured["refresh_settings"] = refresh_settings

    monkeypatch.setattr("enji_guard_cli.auth_impl.auto_refresh._auto_refresh_loop", fake_auto_refresh_loop)
    monkeypatch.setattr(
        "enji_guard_cli.auth.default_settings",
        lambda: type(
            "Settings",
            (),
            {
                "auto_refresh": auto_refresh_settings(),
                "auth": type("Auth", (), {"auth_file": auth_file})(),
            },
        )(),
    )

    async def run_task() -> None:
        task = start_auto_refresh_task()
        assert task is not None
        await task

    asyncio.run(run_task())

    assert captured == {"auth_file": auth_file, "refresh_settings": auto_refresh_settings()}


def test_cli_import_token_reads_from_stdin(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    result = CliRunner().invoke(
        app,
        ["auth", "import-token", "--stdin", "--auth-file", str(auth_file), "--json"],
        input="Bearer token-123\n",
    )

    assert result.exit_code == 0
    payload = cast(ImportPayload, json.loads(result.output))
    assert payload["credential_type"] == "bearer_token"
    stored = cast(StoredAuth, json.loads(auth_file.read_text(encoding="utf-8")))
    assert stored["credential"] == {"type": "bearer_token", "token": "token-123"}


def test_cli_import_cookie_rejects_missing_stdin_flag(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    result = CliRunner().invoke(app, ["auth", "import-cookie", "--auth-file", str(auth_file)])

    assert result.exit_code == 1
    assert not auth_file.exists()
    assert result.stderr == "VALIDATION: use --stdin to avoid storing cookies in shell history\n"


def run_auth_status(task_factory: Callable[[], Awaitable[AuthStatusPayload]]) -> AuthStatusPayload:
    return asyncio.run(task_factory())


def auto_refresh_settings() -> AutoRefreshSettings:
    return AutoRefreshSettings(enabled=True, lead_seconds=300, fallback_seconds=900, retry_seconds=60)


def unsigned_jwt(payload: dict[str, object]) -> str:
    encoded_header = _base64url_json({"alg": "none", "typ": "JWT"})
    encoded_payload = _base64url_json(payload)
    return f"{encoded_header}.{encoded_payload}.signature"


def _base64url_json(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")
