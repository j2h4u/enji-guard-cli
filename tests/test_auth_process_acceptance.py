"""Bounded real-process acceptance tests for durable cookie rotation.

These exercises the coordinator's filesystem contract through independent
interpreters.  The HTTP server is deliberately local and deterministic: no
Fleet credential, Docker daemon, or external network is required in CI.
"""

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest

import enji_guard_cli.auth_session.auto_refresh as auto_refresh
from enji_guard_cli.auth_session.api import import_cookie
from enji_guard_cli.auth_session.coordinator import RefreshCoordinator
from enji_guard_cli.auth_session.state_machine import OutcomeUnknown, Requested
from enji_guard_cli.auth_session.store import (
    AuthLoaded,
    JournalLoaded,
    StoredAuth,
    load_auth,
    load_journal,
    pending_rotation_path,
    write_journal,
)
from enji_guard_cli.transport import EnjiHttpError, EnjiHttpResponse

ROOT = Path(__file__).parents[1]
PROCESS_TIMEOUT_SECONDS = 5.0
POLL_SECONDS = 0.01

_WORKER = textwrap.dedent(
    """
    import asyncio
    import json
    import os
    import time
    from pathlib import Path

    import httpx

    from enji_guard_cli.auth_session.coordinator import CoordinatorDependencies, RefreshCoordinator
    from enji_guard_cli.auth_session.store import StoredAuth
    from enji_guard_cli.transport import EnjiHttpError, EnjiHttpResponse

    async def _run() -> int:
        start_gate = os.environ.get("PROCESS_ACCEPTANCE_START_GATE")
        if start_gate is not None:
            deadline = time.monotonic() + 3
            while not Path(start_gate).exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("process start gate timed out")
                time.sleep(0.005)

        class Exchange:
            async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
                async with httpx.AsyncClient(timeout=3) as client:
                    response = await client.post(
                        os.environ["PROCESS_ACCEPTANCE_REFRESH_URL"],
                        headers={"Cookie": source["credential"]["cookie_header"]},
                    )
                return EnjiHttpResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    content=response.content,
                    set_cookie_headers=tuple(response.headers.get_list("set-cookie")),
                )

        record_file = os.environ.get("PROCESS_ACCEPTANCE_TELEMETRY_FILE")
        crash_sink = os.environ.get("PROCESS_ACCEPTANCE_CRASH_SINK") == "1"

        def sink(_logger: object, _level: int, event: str, fields: object) -> bool:
            if record_file is not None:
                with Path(record_file).open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"event": event, "fields": fields}, sort_keys=True) + "\\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            if crash_sink:
                os._exit(23)
            return True

        dependencies = CoordinatorDependencies(outcome_sink=sink if record_file is not None else None)
        coordinator = RefreshCoordinator(
            Path(os.environ["PROCESS_ACCEPTANCE_AUTH_FILE"]),
            Exchange(),
            terminal_wait_seconds=float(os.environ.get("PROCESS_ACCEPTANCE_TERMINAL_WAIT", "0.15")),
            dependencies=dependencies,
        )
        try:
            if os.environ["PROCESS_ACCEPTANCE_ACTION"] == "recover":
                await coordinator.recover_startup()
            else:
                await coordinator.refresh()
        except EnjiHttpError as exc:
            print(json.dumps({"code": exc.code}))
        return 0

    raise SystemExit(asyncio.run(_run()))
    """
)

_LOCKER = textwrap.dedent(
    """
    import os
    import time
    from pathlib import Path

    from enji_guard_cli.auth_session.store import auth_file_lock

    auth_file = Path(os.environ["PROCESS_ACCEPTANCE_AUTH_FILE"])
    ready = Path(os.environ["PROCESS_ACCEPTANCE_LOCK_READY"])
    release = Path(os.environ["PROCESS_ACCEPTANCE_LOCK_RELEASE"])
    with auth_file_lock(auth_file):
        ready.touch()
        deadline = time.monotonic() + 3
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("lock release timed out")
            time.sleep(0.005)
    """
)


class _RefreshServer(ThreadingHTTPServer):
    request_count: int
    request_seen: threading.Event
    release_response: threading.Event
    hold_response: bool
    response_status: int

    def __init__(self, *, hold_response: bool, response_status: int) -> None:
        super().__init__(("127.0.0.1", 0), _RefreshHandler)
        self.request_count = 0
        self.request_seen = threading.Event()
        self.release_response = threading.Event()
        self.hold_response = hold_response
        self.response_status = response_status

    @property
    def url(self) -> str:
        host, port = cast(tuple[str, int], self.server_address)
        return f"http://{host}:{port}/api/v1/auth/refresh"


class _RefreshHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server = cast(_RefreshServer, self.server)
        content_length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(content_length)
        server.request_count += 1
        server.request_seen.set()
        if server.hold_response:
            assert server.release_response.wait(PROCESS_TIMEOUT_SECONDS)
        try:
            self.send_response(server.response_status)
            self.send_header("Content-Type", "application/json")
            if server.response_status == 200:
                self.send_header("Set-Cookie", "access_token=rotated; Path=/; HttpOnly")
                self.send_header("Set-Cookie", "refresh_token=rotated; Path=/api/v1/auth; HttpOnly")
            self.end_headers()
            self.wfile.write(b"{}")
        except BrokenPipeError:
            pass

    def log_message(self, format: str, *args: object) -> None:
        _ = format, args


@contextmanager
def _refresh_server(*, hold_response: bool = False, response_status: int = 502) -> Iterator[_RefreshServer]:
    server = _RefreshServer(hold_response=hold_response, response_status=response_status)
    thread = threading.Thread(target=server.serve_forever, name="auth-process-fake-refresh")
    thread.start()
    try:
        yield server
    finally:
        server.release_response.set()
        server.shutdown()
        thread.join(PROCESS_TIMEOUT_SECONDS)
        server.server_close()
        assert not thread.is_alive()


@dataclass(frozen=True, slots=True)
class _WorkerOptions:
    url: str
    action: str = "refresh"
    start_gate: Path | None = None
    telemetry_file: Path | None = None
    crash_sink: bool = False


def _spawn_worker(auth_file: Path, options: _WorkerOptions) -> subprocess.Popen[str]:
    environment = os.environ | {
        "PROCESS_ACCEPTANCE_ACTION": options.action,
        "PROCESS_ACCEPTANCE_AUTH_FILE": str(auth_file),
        "PROCESS_ACCEPTANCE_REFRESH_URL": options.url,
    }
    if options.start_gate is not None:
        environment["PROCESS_ACCEPTANCE_START_GATE"] = str(options.start_gate)
    if options.telemetry_file is not None:
        environment["PROCESS_ACCEPTANCE_TELEMETRY_FILE"] = str(options.telemetry_file)
    if options.crash_sink:
        environment["PROCESS_ACCEPTANCE_CRASH_SINK"] = "1"
    return subprocess.Popen(
        [sys.executable, "-c", _WORKER],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish(process: subprocess.Popen[str], *, expected_code: int = 0) -> str:
    try:
        stdout, stderr = process.communicate(timeout=PROCESS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=PROCESS_TIMEOUT_SECONDS)
        pytest.fail(f"worker timed out: stdout={stdout!r}, stderr={stderr!r}")
    assert process.returncode == expected_code, f"stdout={stdout!r}, stderr={stderr!r}"
    return stdout


def _import_old_cookie(auth_file: Path) -> str:
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)
    return loaded.auth["revision"]


def test_sigkill_after_consumed_post_recovers_unknown_without_replay(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    source_revision = _import_old_cookie(auth_file)

    with _refresh_server(hold_response=True) as server:
        worker = _spawn_worker(auth_file, _WorkerOptions(url=server.url))
        assert server.request_seen.wait(PROCESS_TIMEOUT_SECONDS)
        worker.kill()
        _finish(worker, expected_code=-9)
        server.release_response.set()

        requested = load_journal(auth_file)
        assert isinstance(requested, JournalLoaded)
        assert isinstance(requested.state, Requested)

        _finish(_spawn_worker(auth_file, _WorkerOptions(url=server.url, action="recover")))
        recovered = load_journal(auth_file)
        assert isinstance(recovered, JournalLoaded)
        assert isinstance(recovered.state, OutcomeUnknown)
        assert recovered.state.source_revision == source_revision
        assert server.request_count == 1


def test_multiprocess_contention_dispatches_at_most_one_post_per_revision(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    _import_old_cookie(auth_file)
    start_gate = tmp_path / "start"

    with _refresh_server() as server:
        options = _WorkerOptions(url=server.url, start_gate=start_gate)
        first = _spawn_worker(auth_file, options)
        second = _spawn_worker(auth_file, options)
        start_gate.touch()
        _finish(first)
        _finish(second)
        assert server.request_count == 1


def test_process_import_at_request_boundary_cannot_be_overwritten(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    _import_old_cookie(auth_file)

    with _refresh_server(hold_response=True, response_status=200) as server:
        worker = _spawn_worker(auth_file, _WorkerOptions(url=server.url))
        assert server.request_seen.wait(PROCESS_TIMEOUT_SECONDS)
        import_cookie("access_token=imported; refresh_token=imported", auth_file)
        server.release_response.set()
        _finish(worker)

        loaded = load_auth(auth_file)
        assert isinstance(loaded, AuthLoaded)
        assert loaded.auth["credential"] == {
            "type": "cookie",
            "cookie_header": "access_token=imported; refresh_token=imported",
        }
        assert not pending_rotation_path(auth_file).exists()
        assert server.request_count == 1


def test_slow_network_and_contended_flock_keep_asyncio_heartbeat_ticking(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    _import_old_cookie(auth_file)
    lock_ready = tmp_path / "lock-ready"
    lock_release = tmp_path / "lock-release"
    locker_environment = os.environ | {
        "PROCESS_ACCEPTANCE_AUTH_FILE": str(auth_file),
        "PROCESS_ACCEPTANCE_LOCK_READY": str(lock_ready),
        "PROCESS_ACCEPTANCE_LOCK_RELEASE": str(lock_release),
    }
    locker = subprocess.Popen(
        [sys.executable, "-c", _LOCKER],
        cwd=ROOT,
        env=locker_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert _wait_for_path(lock_ready)

        class SlowExchange:
            async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
                _ = source
                await asyncio.sleep(0.05)
                return EnjiHttpResponse(status_code=502, headers={}, content=b"slow upstream")

        async def scenario() -> int:
            heartbeat_count = 0
            stop = asyncio.Event()

            async def heartbeat() -> None:
                nonlocal heartbeat_count
                while not stop.is_set():
                    heartbeat_count += 1
                    await asyncio.sleep(POLL_SECONDS)

            heartbeat_task = asyncio.create_task(heartbeat())
            refresh_task = asyncio.create_task(RefreshCoordinator(auth_file, SlowExchange()).refresh())
            await asyncio.sleep(0.06)
            assert heartbeat_count >= 3
            lock_release.touch()
            with pytest.raises(EnjiHttpError, match="outcome is unknown"):
                await asyncio.wait_for(refresh_task, timeout=PROCESS_TIMEOUT_SECONDS)
            stop.set()
            await asyncio.wait_for(heartbeat_task, timeout=PROCESS_TIMEOUT_SECONDS)
            return heartbeat_count

        assert asyncio.run(scenario()) >= 3
    finally:
        lock_release.touch()
        _finish(locker)


def test_watcher_disabled_revision_polling_detects_atomic_credential_replacement(tmp_path: Path) -> None:
    auth_file = tmp_path / "bind-mounted-auth.json"
    source_revision = _import_old_cookie(auth_file)

    async def watcher_disabled(_path: Path) -> AsyncGenerator[None]:
        await asyncio.Future[None]()
        yield None

    async def scenario() -> None:
        first_read = asyncio.Event()
        reads = 0

        async def unused_refresh(_path: Path, _client: object) -> StoredAuth:
            raise AssertionError("watcher-disabled polling must not refresh")

        def revision_reader(path: Path) -> str | None:
            nonlocal reads
            reads += 1
            first_read.set()
            loaded = load_auth(path)
            return loaded.auth["revision"] if isinstance(loaded, AuthLoaded) else None

        dependencies = auto_refresh.AutoRefreshLoopDependencies(
            sleep_seconds_fn=lambda **_kwargs: 0,
            load_sleep_seconds_stored_auth_fn=lambda _path: None,
            cookie_refresh_sleep_seconds_fn=lambda *_args, **_kwargs: 0,
            refresh_stored_cookie_auth_fn=unused_refresh,
            log_event_fn=lambda *_args, **_kwargs: None,
            logger=auto_refresh.logging.getLogger("test"),
            client_factory=_UnusedClient,
            credential_changes_fn=watcher_disabled,
            revision_reader=revision_reader,
        )
        waiting = asyncio.create_task(
            auto_refresh._wait_for_credential_change(
                auth_file=auth_file,
                expected_revision=source_revision,
                timeout_seconds=1,
                poll_seconds=POLL_SECONDS,
                dependencies=dependencies,
            )
        )
        await asyncio.wait_for(first_read.wait(), timeout=PROCESS_TIMEOUT_SECONDS)
        import_cookie("access_token=replaced; refresh_token=replaced", auth_file)
        assert await asyncio.wait_for(waiting, timeout=PROCESS_TIMEOUT_SECONDS) is True
        assert reads >= 2
        loaded = load_auth(auth_file)
        assert isinstance(loaded, AuthLoaded)
        assert loaded.auth["revision"] != source_revision
        assert loaded.auth["credential"] == {
            "type": "cookie",
            "cookie_header": "access_token=replaced; refresh_token=replaced",
        }

    asyncio.run(scenario())


def test_process_telemetry_sink_crash_replays_stable_redacted_event_key(tmp_path: Path) -> None:
    sentinel_cookie = "COOKIE_SENTINEL_TOKEN"
    sentinel_reason = "REASON_SENTINEL"
    auth_file = tmp_path / "PATH_SENTINEL" / "auth.json"
    source_revision = _import_old_cookie(auth_file)
    write_journal(auth_file, OutcomeUnknown(source_revision, sentinel_reason))
    telemetry_file = tmp_path / "telemetry.jsonl"
    url = "http://127.0.0.1:9/unused"

    _finish(
        _spawn_worker(
            auth_file,
            _WorkerOptions(url=url, action="recover", telemetry_file=telemetry_file, crash_sink=True),
        ),
        expected_code=23,
    )
    _finish(_spawn_worker(auth_file, _WorkerOptions(url=url, action="recover", telemetry_file=telemetry_file)))

    records = [
        cast(dict[str, object], json.loads(line)) for line in telemetry_file.read_text(encoding="utf-8").splitlines()
    ]
    assert [cast(str, record["event"]) for record in records] == ["enji_auth_rotation_outcome_unknown"] * 2
    assert [cast(dict[str, str], record["fields"]) for record in records] == [
        {"event_key": f"auth-rotation:{source_revision}:outcome_unknown"}
    ] * 2
    emitted = telemetry_file.read_text(encoding="utf-8")
    assert sentinel_cookie not in emitted
    assert sentinel_reason not in emitted
    assert "PATH_SENTINEL" not in emitted


def _wait_for_path(path: Path) -> bool:
    deadline = time.monotonic() + PROCESS_TIMEOUT_SECONDS
    while not path.exists():
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)
    return True


class _UnusedClient:
    async def __aenter__(self) -> _UnusedClient:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        return None
