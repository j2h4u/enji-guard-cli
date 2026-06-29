import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import httpx
import pytest
from typer.testing import CliRunner

from enji_guard_cli.auth import (
    AUTH_REFRESH_ORIGIN,
    AUTH_REFRESH_REFERER,
    AUTH_REFRESH_USER_AGENT,
    AuthError,
    AuthRefreshPayload,
    AuthStatusPayload,
    auth_headers,
    auth_status_async,
    cookie_access_expires_at,
    cookie_refresh_sleep_seconds,
    import_bearer_token,
    import_cookie,
    load_stored_auth,
    merge_set_cookie_headers,
    refresh_auth_async,
)
from enji_guard_cli.auth import StoredAuth as RuntimeStoredAuth
from enji_guard_cli.cli import app
from enji_guard_cli.transport import HttpxEnjiHttpClient


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


def test_merge_set_cookie_headers_updates_existing_cookie_without_keeping_attributes() -> None:
    updated = merge_set_cookie_headers(
        "access=old; refresh=long",
        ["access=new; Path=/; HttpOnly; SameSite=Lax"],
    )

    assert updated.value == "access=new; refresh=long"
    assert updated.count == 2


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
    assert cookie_refresh_sleep_seconds(stored_auth, now, lead_seconds=300, fallback_seconds=900) == 420


def test_cookie_refresh_sleep_seconds_returns_zero_inside_refresh_window(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    expires_at = now + timedelta(minutes=3)
    import_cookie(f"access_token={unsigned_jwt({'exp': int(expires_at.timestamp())})}; refresh_token=long", auth_file)
    stored_auth = load_stored_auth(auth_file)

    assert stored_auth is not None
    assert cookie_refresh_sleep_seconds(stored_auth, now, lead_seconds=300, fallback_seconds=900) == 0


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
    import_cookie("access=old; refresh=long", auth_file)
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
                headers={"Set-Cookie": "access=new; Path=/; HttpOnly"},
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
        ("GET", "/api/v1/auth/me", "access=old; refresh=long"),
        ("POST", "/api/v1/auth/refresh", "access=old; refresh=long"),
        ("GET", "/api/v1/auth/me", "access=new; refresh=long"),
    ]
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access=new; refresh=long"}


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
    import_cookie("access=old; refresh=long", auth_file)
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path))
        if len(captured) == 1:
            return httpx.Response(401, json={"error": {"code": "AUTH_INVALID"}}, request=request)
        if len(captured) == 2:
            return httpx.Response(
                200,
                json={"message": "token refreshed"},
                headers={"Set-Cookie": "access=new; Path=/; HttpOnly"},
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
    import_cookie("access=old; refresh=long", auth_file)
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path))
        if len(captured) == 2:
            return httpx.Response(
                200,
                json={"message": "token refreshed"},
                headers={"Set-Cookie": "access=new; Path=/; HttpOnly"},
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


def unsigned_jwt(payload: dict[str, object]) -> str:
    encoded_header = _base64url_json({"alg": "none", "typ": "JWT"})
    encoded_payload = _base64url_json(payload)
    return f"{encoded_header}.{encoded_payload}.signature"


def _base64url_json(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")
