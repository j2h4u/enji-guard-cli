import asyncio
import base64
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import httpx
import pytest
from typer.testing import CliRunner

import enji_guard_cli.auth_session.auto_refresh as auto_refresh_module
from enji_guard_cli.auth_session.api import (
    AuthStatusPayload,
    _refresh_cookie_auth,
    auth_headers,
    auth_status_async,
    backend_readiness_probe_async,
    cookie_access_expires_at,
    cookie_refresh_sleep_seconds,
    import_bearer_token,
    import_cookie,
    start_auto_refresh_task,
)
from enji_guard_cli.auth_session.api import StoredAuth as RuntimeStoredAuth
from enji_guard_cli.auth_session.cookies import merge_set_cookie_headers, set_cookie_names
from enji_guard_cli.auth_session.coordinator import PreDispatchLocalError, TerminalRevisionRequiredError
from enji_guard_cli.auth_session.models import AuthBackendReadinessResult
from enji_guard_cli.auth_session.ports import AuthOutcomeSink
from enji_guard_cli.auth_session.state_machine import OutcomeUnknown, Rejected, RotationState
from enji_guard_cli.auth_session.store import (
    AuthAbsent,
    AuthClockAnomaly,
    AuthCorrupt,
    AuthIoFailure,
    AuthLoaded,
    AuthUnsupported,
    load_auth,
    write_journal,
)
from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.runtime_observability.auth_coordinator import RuntimeAuthCoordinatorAdapter
from enji_guard_cli.settings import DEFAULT_GUARD_ORIGIN, DEFAULT_GUARD_REFERER, AutoRefreshSettings
from enji_guard_cli.transport import EnjiHttpError, EnjiHttpRequest, EnjiHttpResponse, HttpxEnjiHttpClient

AUTH_REFRESH_ORIGIN = DEFAULT_GUARD_ORIGIN
AUTH_REFRESH_REFERER = DEFAULT_GUARD_REFERER


async def _never_changes(_auth_file: Path) -> AsyncGenerator[None]:
    await asyncio.Event().wait()
    yield


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


def _loaded_auth(path: Path) -> RuntimeStoredAuth:
    loaded = load_auth(path)
    assert isinstance(loaded, AuthLoaded)
    return loaded.auth


async def _refresh_stored_cookie_auth(
    path: Path, client: HttpxEnjiHttpClient, *, outcome_sink: object = None
) -> RuntimeStoredAuth:
    return await _refresh_cookie_auth(
        path, _loaded_auth(path), client, outcome_sink=cast(AuthOutcomeSink | None, outcome_sink)
    )


def load_stored_auth(path: Path) -> RuntimeStoredAuth:
    """Test-only convenience: production has no nullable auth reader."""

    return _loaded_auth(path)


def _test_cookie_auth(revision: str = "old") -> RuntimeStoredAuth:
    return cast(
        RuntimeStoredAuth,
        {
            "version": 2,
            "revision": revision,
            "base_url": "https://fleet.example.test",
            "credential": {"type": "cookie", "cookie_header": "access=old; refresh=old"},
            "imported_at": "2026-01-01T00:00:00+00:00",
        },
    )


def _test_bearer_auth(revision: str = "bearer") -> RuntimeStoredAuth:
    return cast(
        RuntimeStoredAuth,
        {
            "version": 2,
            "revision": revision,
            "base_url": "https://fleet.example.test",
            "credential": {"type": "bearer_token", "token": "test-token"},
            "imported_at": "2026-01-01T00:00:00+00:00",
        },
    )


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


def test_future_credential_import_timestamp_has_stable_clock_anomaly_classification(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("session=abc; refresh=def", auth_file)
    stored = cast(dict[str, object], json.loads(auth_file.read_text(encoding="utf-8")))
    stored["imported_at"] = "9999-12-31T23:59:59+00:00"
    auth_file.write_text(json.dumps(stored), encoding="utf-8")

    status = asyncio.run(auth_status_async(auth_file))
    readiness = asyncio.run(backend_readiness_probe_async(auth_file))

    assert status["code"] == "AUTH_CLOCK_ANOMALY"
    assert status["message"] == "auth file imported_at is in the future"
    assert readiness.failure_code == "AUTH_CLOCK_ANOMALY"
    assert readiness.failure_message == "auth file imported_at is in the future"
    assert readiness.bypass_grace is True


@pytest.mark.parametrize(
    ("state_factory", "expected_code"),
    [
        (lambda revision: Rejected(revision, "rejected"), "AUTH_REFRESH_REJECTED"),
        (lambda revision: OutcomeUnknown(revision, "timeout"), "AUTH_REFRESH_OUTCOME_UNKNOWN"),
    ],
)
def test_terminal_auth_projection_bypasses_status_and_readiness_network(
    tmp_path: Path,
    state_factory: Callable[[str], RotationState],
    expected_code: str,
) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)
    write_journal(auth_file, state_factory(loaded.auth["revision"]))
    requests: list[EnjiHttpRequest] = []

    class Client:
        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            requests.append(request)
            raise AssertionError("terminal auth projection must not perform HTTP")

    async def observe() -> tuple[AuthStatusPayload, AuthBackendReadinessResult]:
        return (
            await auth_status_async(auth_file, Client()),
            await backend_readiness_probe_async(auth_file, Client()),
        )

    status, readiness = asyncio.run(observe())

    assert status["code"] == expected_code
    assert "import a fresh browser credential" in cast(str, status["message"])
    assert readiness.failure_code == expected_code
    assert readiness.bypass_grace is True
    assert requests == []


@pytest.mark.parametrize(
    ("loaded", "expected_code"),
    [
        (AuthAbsent(), "AUTH_REQUIRED"),
        (AuthCorrupt("invalid JSON"), "AUTH_CORRUPT"),
        (AuthUnsupported(3), "AUTH_UNSUPPORTED"),
        (AuthIoFailure("read credential", OSError("denied")), "AUTH_IO_FAILURE"),
        (AuthClockAnomaly("imported_at"), "AUTH_CLOCK_ANOMALY"),
    ],
)
def test_auto_refresh_loop_observes_non_cookie_storage_states_without_dispatch(
    loaded: object, expected_code: str
) -> None:
    async def exercise() -> None:
        observed = asyncio.Event()
        dispatches = 0
        observed_codes: list[str] = []

        async def no_dispatch(*_args: object) -> RuntimeStoredAuth:
            nonlocal dispatches
            dispatches += 1
            raise AssertionError("invalid credential state must not dispatch")

        def log_event(*args: object) -> None:
            if args[2] == "enji_auth_auto_refresh_observing_credential":
                observed_codes.append(cast(dict[str, str], args[3])["code"])
                observed.set()

        task = asyncio.create_task(
            auto_refresh_module._auto_refresh_loop(
                auth_file=Path("auth.json"),
                refresh_settings=AutoRefreshSettings(enabled=True, lead_seconds=300, fallback_seconds=900),
                dependencies=auto_refresh_module.AutoRefreshLoopDependencies(
                    load_auth_fn=lambda _path: cast(auto_refresh_module.AuthLoadResult, loaded),
                    cookie_refresh_sleep_seconds_fn=lambda *_args, **_kwargs: 0,
                    refresh_cookie_auth_fn=no_dispatch,
                    log_event_fn=log_event,
                    logger=auto_refresh_module.logging.getLogger("test"),
                    client_factory=_TestClient,
                    credential_changes_fn=_never_changes,
                ),
            )
        )
        await asyncio.wait_for(observed.wait(), timeout=1)
        assert task.done() is False
        assert dispatches == 0
        assert observed_codes == [expected_code]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_auto_refresh_loop_observes_bearer_then_schedules_cookie_revision_without_posting() -> None:
    async def exercise() -> None:
        loaded = AuthLoaded(_test_bearer_auth())
        revision = "bearer"
        changed = asyncio.Event()
        scheduled = asyncio.Event()
        dispatches = 0

        async def changes(_auth_file: Path) -> AsyncGenerator[None]:
            await changed.wait()
            yield
            await asyncio.Event().wait()

        def sleep_seconds(**_kwargs: object) -> int:
            scheduled.set()
            return 3600

        async def no_dispatch(*_args: object) -> RuntimeStoredAuth:
            nonlocal dispatches
            dispatches += 1
            raise AssertionError("scheduled cookie must not dispatch before its due time")

        task = asyncio.create_task(
            auto_refresh_module._auto_refresh_loop(
                auth_file=Path("auth.json"),
                refresh_settings=_scheduler_settings(),
                dependencies=_loop_dependencies(
                    changes=changes,
                    overrides=_LoopOverrides(
                        load_auth_fn=lambda _path: loaded,
                        revision_reader=lambda _path: revision,
                        cookie_refresh_sleep_seconds_fn=sleep_seconds,
                        refresh_fn=no_dispatch,
                    ),
                ),
            )
        )
        await asyncio.sleep(0)
        assert dispatches == 0

        loaded = AuthLoaded(_test_cookie_auth("cookie"))
        revision = "cookie"
        changed.set()
        await asyncio.wait_for(scheduled.wait(), timeout=1)
        assert dispatches == 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_auto_refresh_loop_stops_cookie_dispatch_after_bearer_revision() -> None:
    async def exercise() -> None:
        loaded = AuthLoaded(_test_cookie_auth())
        revision = "old"
        observed_bearer = asyncio.Event()
        dispatches = 0

        async def changes(_auth_file: Path) -> AsyncGenerator[None]:
            await asyncio.Event().wait()
            yield

        async def sleep(_seconds: float) -> None:
            nonlocal loaded, revision
            loaded = AuthLoaded(_test_bearer_auth("bearer"))
            revision = "bearer"

        def log_event(*args: object) -> None:
            fields = cast(dict[str, str], args[3])
            if args[2] == "enji_auth_auto_refresh_observing_credential" and fields == {
                "credential_type": "bearer_token"
            }:
                observed_bearer.set()

        async def no_dispatch(*_args: object) -> RuntimeStoredAuth:
            nonlocal dispatches
            dispatches += 1
            raise AssertionError("bearer replacement must prevent cookie dispatch")

        task = asyncio.create_task(
            auto_refresh_module._auto_refresh_loop(
                auth_file=Path("auth.json"),
                refresh_settings=_scheduler_settings(),
                dependencies=_loop_dependencies(
                    changes=changes,
                    overrides=_LoopOverrides(
                        load_auth_fn=lambda _path: loaded,
                        revision_reader=lambda _path: revision,
                        sleep_fn=sleep,
                        cookie_refresh_sleep_seconds_fn=lambda **_kwargs: 60,
                        refresh_fn=no_dispatch,
                        log_event_fn=log_event,
                    ),
                ),
            )
        )
        await asyncio.wait_for(observed_bearer.wait(), timeout=1)
        assert dispatches == 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_auto_refresh_loop_survives_terminal_cookie_response_error() -> None:
    async def exercise() -> None:
        refresh_attempted = asyncio.Event()
        credential_imported = asyncio.Event()

        async def changes(_auth_file: Path) -> AsyncGenerator[None]:
            await credential_imported.wait()
            yield
            await asyncio.Event().wait()

        async def terminal_refresh(*_args: object) -> RuntimeStoredAuth:
            refresh_attempted.set()
            raise TerminalRevisionRequiredError("r1", message="refresh outcome is unknown")

        class Client:
            async def __aenter__(self) -> Client:
                return self

            async def __aexit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
                return None

        task = asyncio.create_task(
            auto_refresh_module._auto_refresh_loop(
                auth_file=Path("auth.json"),
                refresh_settings=AutoRefreshSettings(enabled=True, lead_seconds=300, fallback_seconds=900),
                dependencies=auto_refresh_module.AutoRefreshLoopDependencies(
                    load_auth_fn=lambda _path: AuthLoaded(_test_cookie_auth("r1")),
                    cookie_refresh_sleep_seconds_fn=lambda *_args, **_kwargs: 0,
                    refresh_cookie_auth_fn=lambda _path, _auth, _client: terminal_refresh(),
                    log_event_fn=lambda *_args, **_kwargs: None,
                    logger=auto_refresh_module.logging.getLogger("test"),
                    client_factory=Client,
                    credential_changes_fn=changes,
                    revision_reader=lambda _path: "r1",
                ),
            )
        )
        await asyncio.wait_for(refresh_attempted.wait(), timeout=1)
        assert not task.done()

        credential_imported.set()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_credential_change_wait_survives_timeout() -> None:
    async def exercise() -> None:
        async def changes(_auth_file: Path) -> AsyncGenerator[None]:
            await asyncio.Event().wait()
            yield

        async def unused_refresh(_path: Path, _auth: RuntimeStoredAuth, _client: object) -> RuntimeStoredAuth:
            return cast(RuntimeStoredAuth, {})

        dependencies = auto_refresh_module.AutoRefreshLoopDependencies(
            load_auth_fn=lambda _path: AuthLoaded(_test_cookie_auth()),
            cookie_refresh_sleep_seconds_fn=lambda *_args, **_kwargs: 0,
            refresh_cookie_auth_fn=unused_refresh,
            log_event_fn=lambda *_args, **_kwargs: None,
            logger=auto_refresh_module.logging.getLogger("test"),
            client_factory=_TestClient,
            credential_changes_fn=changes,
            revision_reader=lambda _path: "r1",
        )
        changed = await auto_refresh_module._wait_for_credential_change(
            auth_file=Path("auth.json"),
            expected_revision="r1",
            timeout_seconds=0,
            poll_seconds=1,
            dependencies=dependencies,
        )

        assert changed is False

    asyncio.run(exercise())


def test_revision_polling_detects_import_when_watcher_misses_bind_mount_event() -> None:
    async def exercise() -> None:
        revision = "old"
        clock = 0.0
        sleeps: list[float] = []

        async def changes(_auth_file: Path) -> AsyncGenerator[None]:
            await asyncio.Event().wait()
            yield

        async def sleep(seconds: float) -> None:
            nonlocal clock, revision
            sleeps.append(seconds)
            clock += seconds
            revision = "imported"

        dependencies = _loop_dependencies(
            changes=changes,
            overrides=_LoopOverrides(
                revision_reader=lambda _path: revision, monotonic_fn=lambda: clock, sleep_fn=sleep
            ),
        )

        assert await auto_refresh_module._wait_for_credential_change(
            auth_file=Path("auth.json"),
            expected_revision="old",
            timeout_seconds=60,
            poll_seconds=5,
            dependencies=dependencies,
        )
        assert sleeps == [5]

    asyncio.run(exercise())


def test_watcher_exception_degrades_to_bounded_revision_polling() -> None:
    async def exercise() -> None:
        clock = 0.0
        events: list[str] = []

        async def broken_watcher(_auth_file: Path) -> AsyncGenerator[None]:
            if _auth_file.name == "":
                yield
            raise RuntimeError("watcher unavailable")

        async def sleep(seconds: float) -> None:
            nonlocal clock
            clock += seconds

        dependencies = _loop_dependencies(
            changes=broken_watcher,
            overrides=_LoopOverrides(
                monotonic_fn=lambda: clock,
                sleep_fn=sleep,
                log_event_fn=lambda _logger, _level, event, _fields: events.append(event),
            ),
        )

        assert not await auto_refresh_module._wait_for_credential_change(
            auth_file=Path("auth.json"),
            expected_revision="old",
            timeout_seconds=2,
            poll_seconds=1,
            dependencies=dependencies,
        )
        assert events == ["enji_auth_credential_watcher_failed"]

    asyncio.run(exercise())


def test_long_schedule_is_interrupted_by_revision_polling_before_refresh() -> None:
    async def exercise() -> None:
        revision = "old"
        clock = 0.0
        scheduled = 0
        refreshes = 0

        async def changes(_auth_file: Path) -> AsyncGenerator[None]:
            await asyncio.Event().wait()
            yield

        async def sleep(seconds: float) -> None:
            nonlocal clock, revision
            clock += seconds
            if revision == "old":
                revision = "imported"
            else:
                raise asyncio.CancelledError

        def sleep_seconds(**_kwargs: object) -> int:
            nonlocal scheduled
            scheduled += 1
            return 3600

        async def refresh(*_args: object) -> RuntimeStoredAuth:
            nonlocal refreshes
            refreshes += 1
            raise AssertionError("import should interrupt a long schedule")

        dependencies = _loop_dependencies(
            changes=changes,
            overrides=_LoopOverrides(
                revision_reader=lambda _path: revision,
                monotonic_fn=lambda: clock,
                sleep_fn=sleep,
                refresh_fn=refresh,
                cookie_refresh_sleep_seconds_fn=sleep_seconds,
                load_auth_fn=lambda _path: AuthLoaded(_test_cookie_auth(revision)),
            ),
        )
        with pytest.raises(asyncio.CancelledError):
            await auto_refresh_module._auto_refresh_loop(
                auth_file=Path("auth.json"),
                refresh_settings=_scheduler_settings(),
                dependencies=dependencies,
            )

        assert scheduled == 2
        assert refreshes == 0

    asyncio.run(exercise())


def test_pre_dispatch_retry_is_bounded_and_never_posts_before_reservation() -> None:
    async def exercise() -> None:
        clock = 0.0
        refreshes = 0
        sleeps: list[float] = []

        async def changes(_auth_file: Path) -> AsyncGenerator[None]:
            await asyncio.Event().wait()
            yield

        async def sleep(seconds: float) -> None:
            nonlocal clock
            sleeps.append(seconds)
            clock += seconds
            if clock >= 13:
                raise asyncio.CancelledError

        async def no_post_before_reservation(*_args: object) -> RuntimeStoredAuth:
            nonlocal refreshes
            refreshes += 1
            raise PreDispatchLocalError(OSError("reservation fsync failed"))

        dependencies = _loop_dependencies(
            changes=changes,
            overrides=_LoopOverrides(
                monotonic_fn=lambda: clock,
                sleep_fn=sleep,
                refresh_fn=no_post_before_reservation,
                cookie_refresh_sleep_seconds_fn=lambda **_kwargs: 0,
            ),
        )
        settings = _scheduler_settings(pre_dispatch_retry_limit=2, fallback_seconds=100)
        with pytest.raises(asyncio.CancelledError):
            await auto_refresh_module._auto_refresh_loop(
                auth_file=Path("auth.json"), refresh_settings=settings, dependencies=dependencies
            )

        assert refreshes == 3
        assert sleeps == [1, 2, 10]

    asyncio.run(exercise())


def test_requested_outcome_waits_for_import_without_retrying_dispatch() -> None:
    async def exercise() -> None:
        revision = "r1"
        clock = 0.0
        schedules = 0
        dispatches = 0

        async def changes(_auth_file: Path) -> AsyncGenerator[None]:
            await asyncio.Event().wait()
            yield

        async def sleep(seconds: float) -> None:
            nonlocal clock, revision
            clock += seconds
            if revision == "r1":
                revision = "imported"
            else:
                raise asyncio.CancelledError

        def sleep_seconds(**_kwargs: object) -> int:
            nonlocal schedules
            schedules += 1
            return 0 if schedules == 1 else 3600

        async def requested(*_args: object) -> RuntimeStoredAuth:
            nonlocal dispatches
            dispatches += 1
            raise TerminalRevisionRequiredError("r1", message="already requested")

        dependencies = _loop_dependencies(
            changes=changes,
            overrides=_LoopOverrides(
                revision_reader=lambda _path: revision,
                monotonic_fn=lambda: clock,
                sleep_fn=sleep,
                refresh_fn=requested,
                cookie_refresh_sleep_seconds_fn=sleep_seconds,
                load_auth_fn=lambda _path: AuthLoaded(_test_cookie_auth(revision)),
            ),
        )
        with pytest.raises(asyncio.CancelledError):
            await auto_refresh_module._auto_refresh_loop(
                auth_file=Path("auth.json"),
                refresh_settings=_scheduler_settings(),
                dependencies=dependencies,
            )

        assert dispatches == 1

    asyncio.run(exercise())


def test_wait_cancellation_closes_watcher_generator() -> None:
    async def exercise() -> None:
        closed = asyncio.Event()
        sleeping = asyncio.Event()

        async def changes(_auth_file: Path) -> AsyncGenerator[None]:
            try:
                await asyncio.Event().wait()
                yield
            finally:
                closed.set()

        async def sleep(_seconds: float) -> None:
            sleeping.set()
            await asyncio.Event().wait()

        dependencies = _loop_dependencies(changes=changes, overrides=_LoopOverrides(sleep_fn=sleep))
        task = asyncio.create_task(
            auto_refresh_module._wait_for_credential_change(
                auth_file=Path("auth.json"),
                expected_revision="old",
                timeout_seconds=60,
                poll_seconds=1,
                dependencies=dependencies,
            )
        )
        await sleeping.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(closed.wait(), timeout=1)

    asyncio.run(exercise())


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


def test_auth_status_does_not_refresh_or_replay_on_invalid_cookie(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path))
        return httpx.Response(401, json={"error": {"code": "AUTH_INVALID"}}, request=request)

    async def run_status() -> AuthStatusPayload:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await auth_status_async(auth_file, HttpxEnjiHttpClient(client))

    status = run_auth_status(run_status)

    assert status["authenticated"] is False
    assert status["code"] == "AUTH_REQUIRED"
    assert captured == [("GET", "/api/v1/auth/me")]


def test_backend_readiness_probe_does_not_refresh_on_auth_invalid(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path))
        return httpx.Response(401, json={"error": {"code": "AUTH_INVALID"}}, request=request)

    async def run_probe() -> AuthBackendReadinessResult:
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

    async def run_refresh() -> RuntimeStoredAuth:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _refresh_stored_cookie_auth(auth_file, HttpxEnjiHttpClient(client))

    rotated = asyncio.run(run_refresh())

    assert rotated["credential"] == {
        "type": "cookie",
        "cookie_header": f"access_token={unsigned_jwt({'exp': int(expires_at.timestamp())})}; refresh_token=new",
    }
    assert captured == [("POST", "/api/v1/auth/refresh", "access_token=old; refresh_token=old")]
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {
        "type": "cookie",
        "cookie_header": f"access_token={unsigned_jwt({'exp': int(expires_at.timestamp())})}; refresh_token=new",
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

    async def run_refresh() -> RuntimeStoredAuth:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _refresh_stored_cookie_auth(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(EnjiHttpError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "AUTH_IMPORT_REQUIRED"
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access_token=old; refresh_token=old"}


def test_refresh_auth_marks_transient_response_unknown_without_persisting_cookies(tmp_path: Path) -> None:
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

    async def run_refresh() -> RuntimeStoredAuth:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _refresh_stored_cookie_auth(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(EnjiHttpError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "AUTH_IMPORT_REQUIRED"
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access_token=old; refresh_token=old"}


def test_refresh_auth_does_not_persist_incomplete_transient_cookie_rotation(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            headers=[("Set-Cookie", f"access_token={unsigned_jwt({'exp': 1782744000})}; Path=/; HttpOnly")],
            request=request,
        )

    async def run_refresh() -> RuntimeStoredAuth:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _refresh_stored_cookie_auth(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(EnjiHttpError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "AUTH_IMPORT_REQUIRED"
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

    async def run_refresh() -> RuntimeStoredAuth:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _refresh_stored_cookie_auth(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(EnjiHttpError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "AUTH_IMPORT_REQUIRED"
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

    async def run_refresh() -> RuntimeStoredAuth:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _refresh_stored_cookie_auth(auth_file, HttpxEnjiHttpClient(client))

    with pytest.raises(EnjiHttpError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "AUTH_IMPORT_REQUIRED"
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access_token=old; refresh_token=old"}


@pytest.mark.parametrize("status_code", [401, 403])
def test_refresh_auth_does_not_persist_cookies_from_auth_failure(tmp_path: Path, status_code: int) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    events: list[tuple[str, Mapping[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers=[
                ("Set-Cookie", f"access_token={unsigned_jwt({'exp': 1782744000})}; Path=/; HttpOnly"),
                ("Set-Cookie", "refresh_token=rejected; Path=/api/v1/auth; HttpOnly"),
            ],
            request=request,
        )

    async def run_refresh() -> RuntimeStoredAuth:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _refresh_stored_cookie_auth(
                auth_file,
                HttpxEnjiHttpClient(client),
                outcome_sink=lambda logger, level, event, fields: events.append((event, fields)) or True,
            )

    with pytest.raises(EnjiHttpError) as exc_info:
        asyncio.run(run_refresh())

    assert exc_info.value.code == "AUTH_IMPORT_REQUIRED"
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access_token=old; refresh_token=old"}


def test_start_auto_refresh_task_observes_bearer_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)

    captured: list[Path] = []

    async def fake_auto_refresh_loop(*, auth_file: Path, **_kwargs: object) -> None:
        captured.append(auth_file)

    monkeypatch.setattr("enji_guard_cli.auth_session.auto_refresh._auto_refresh_loop", fake_auto_refresh_loop)
    monkeypatch.setattr(
        "enji_guard_cli.auth_session.api.default_settings",
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
    assert captured == [auth_file]


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

    monkeypatch.setattr("enji_guard_cli.auth_session.auto_refresh._auto_refresh_loop", fake_auto_refresh_loop)
    monkeypatch.setattr(
        "enji_guard_cli.auth_session.api.default_settings",
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


def test_start_auto_refresh_task_uses_explicit_auth_file_without_default_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    custom_auth_file = tmp_path / "custom" / "auth.json"
    default_auth_file = tmp_path / "default" / "auth.json"
    captured: dict[str, object] = {}

    async def fake_auto_refresh_loop(
        *, auth_file: Path, refresh_settings: AutoRefreshSettings, **_kwargs: object
    ) -> None:
        captured["auth_file"] = auth_file
        captured["refresh_settings"] = refresh_settings

    monkeypatch.setattr("enji_guard_cli.auth_session.auto_refresh._auto_refresh_loop", fake_auto_refresh_loop)
    monkeypatch.setattr(
        "enji_guard_cli.auth_session.api.default_settings",
        lambda: type(
            "Settings",
            (),
            {
                "auto_refresh": auto_refresh_settings(),
                "auth": type("Auth", (), {"auth_file": default_auth_file})(),
            },
        )(),
    )

    async def run_task() -> None:
        task = start_auto_refresh_task(custom_auth_file)
        assert task is not None
        await task

    asyncio.run(run_task())

    assert captured["auth_file"] == custom_auth_file
    assert captured["auth_file"] != default_auth_file


def test_runtime_auth_adapter_wires_runtime_owned_telemetry_and_durable_outcome_sinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured_dependencies: list[auto_refresh_module.AutoRefreshTaskDependencies] = []
    events: list[tuple[str, dict[str, object]]] = []

    def event_sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> None:
        _ = logger, level
        events.append((event, dict(fields)))

    def fake_start_auto_refresh_task(
        *,
        auth_file: Path,
        refresh_settings: AutoRefreshSettings,
        dependencies: auto_refresh_module.AutoRefreshTaskDependencies,
    ) -> None:
        assert refresh_settings.enabled is True
        captured_dependencies.append(dependencies)
        assert auth_file == tmp_path / ("custom-auth.json" if len(captured_dependencies) == 1 else "isolated-auth.json")

    monkeypatch.setattr(auto_refresh_module, "start_auto_refresh_task", fake_start_auto_refresh_task)

    outcome_events: list[tuple[str, dict[str, object]]] = []

    def outcome_sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> bool:
        _ = logger, level
        outcome_events.append((event, dict(fields)))
        return True

    adapter = RuntimeAuthCoordinatorAdapter(
        tmp_path / "custom-auth.json",
        event_sink=event_sink,
        outcome_sink=outcome_sink,
    )
    assert adapter.start_background_refresh_task() is None

    dependencies = captured_dependencies[0].loop_dependencies
    for event in (
        "enji_auth_auto_refresh_scheduled",
        "enji_auth_auto_refresh_schedule_failed",
    ):
        dependencies.log_event_fn(logging.getLogger("test"), logging.INFO, event, {"safe": True})

    assert [event for event, _fields in events] == [
        "enji_auth_auto_refresh_scheduled",
        "enji_auth_auto_refresh_schedule_failed",
    ]
    assert all(fields == {"safe": True} for _event, fields in events)

    isolated_adapter = RuntimeAuthCoordinatorAdapter(
        tmp_path / "isolated-auth.json",
        event_sink=event_sink,
        outcome_sink=outcome_sink,
    )
    assert isolated_adapter.start_background_refresh_task() is None
    isolated_dependencies = captured_dependencies[1].loop_dependencies
    isolated_dependencies.log_event_fn(logging.getLogger("test"), logging.INFO, "leak-check", {})
    assert [event for event, _fields in events] == [
        "enji_auth_auto_refresh_scheduled",
        "enji_auth_auto_refresh_schedule_failed",
        "leak-check",
    ]
    assert outcome_events == []


def test_cli_import_bearer_reads_from_stdin(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    result = CliRunner().invoke(
        app,
        ["auth", "import-bearer", "--stdin", "--auth-file", str(auth_file), "--json"],
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
    return AutoRefreshSettings(enabled=True, lead_seconds=300, fallback_seconds=900)


def _scheduler_settings(
    *,
    fallback_seconds: int = 100,
    pre_dispatch_retry_limit: int = 3,
) -> AutoRefreshSettings:
    return AutoRefreshSettings(
        enabled=True,
        lead_seconds=300,
        fallback_seconds=fallback_seconds,
        revision_poll_seconds=10,
        pre_dispatch_retry_limit=pre_dispatch_retry_limit,
        pre_dispatch_retry_initial_seconds=1,
        pre_dispatch_retry_max_seconds=10,
        pre_dispatch_retry_jitter_seconds=0,
    )


@dataclass(frozen=True)
class _LoopOverrides:
    revision_reader: Callable[[Path], str | None] = lambda _path: "old"
    monotonic_fn: Callable[[], float] = lambda: 0
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep
    refresh_fn: Callable[[Path, RuntimeStoredAuth, object], Awaitable[RuntimeStoredAuth]] | None = None
    cookie_refresh_sleep_seconds_fn: Callable[..., int] | None = None
    load_auth_fn: Callable[[Path], AuthLoaded] = lambda _path: AuthLoaded(_test_cookie_auth())
    log_event_fn: Callable[..., None] = lambda *_args, **_kwargs: None


def _loop_dependencies(
    *, changes: Callable[[Path], AsyncGenerator[None]], overrides: _LoopOverrides | None = None
) -> auto_refresh_module.AutoRefreshLoopDependencies:
    resolved_overrides = overrides if overrides is not None else _LoopOverrides()

    async def refresh(_path: Path, _auth: RuntimeStoredAuth, _client: object) -> RuntimeStoredAuth:
        return cast(RuntimeStoredAuth, {})

    return auto_refresh_module.AutoRefreshLoopDependencies(
        load_auth_fn=resolved_overrides.load_auth_fn,
        cookie_refresh_sleep_seconds_fn=(
            resolved_overrides.cookie_refresh_sleep_seconds_fn
            if resolved_overrides.cookie_refresh_sleep_seconds_fn is not None
            else lambda **_kwargs: 0
        ),
        refresh_cookie_auth_fn=resolved_overrides.refresh_fn if resolved_overrides.refresh_fn is not None else refresh,
        log_event_fn=resolved_overrides.log_event_fn,
        logger=auto_refresh_module.logging.getLogger("test"),
        client_factory=_TestClient,
        credential_changes_fn=changes,
        revision_reader=resolved_overrides.revision_reader,
        monotonic_fn=resolved_overrides.monotonic_fn,
        random_fn=lambda: 0,
        sleep_fn=resolved_overrides.sleep_fn,
    )


class _TestClient:
    async def __aenter__(self) -> _TestClient:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        return None


def unsigned_jwt(payload: dict[str, object]) -> str:
    encoded_header = _base64url_json({"alg": "none", "typ": "JWT"})
    encoded_payload = _base64url_json(payload)
    return f"{encoded_header}.{encoded_payload}.signature"


def _base64url_json(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")
