import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

import pytest

import enji_guard_cli.auth_session.cookies as cookies_module
from enji_guard_cli.auth_session.api import import_cookie
from enji_guard_cli.auth_session.coordinator import (
    CoordinatorDependencies,
    RefreshCoordinator,
    TerminalRevisionRequiredError,
    import_credential,
)
from enji_guard_cli.auth_session.models import StoredAuth
from enji_guard_cli.auth_session.state_machine import (
    Begin,
    DeleteJournal,
    DispatchBegun,
    DispatchExchange,
    Effect,
    ExchangeOutcomeUnknown,
    ExchangeRejected,
    ExchangeSucceeded,
    Imported,
    InvalidTransitionError,
    OutcomeUnknown,
    PersistJournal,
    PersistReplacement,
    Ready,
    Recover,
    Rejected,
    Requested,
    Reserved,
    Rotated,
    RotationEvent,
    RotationState,
    WaitForTerminalRevision,
    transition,
)
from enji_guard_cli.auth_session.store import (
    IMPORTED_AT_FUTURE_TOLERANCE,
    AuthClockAnomaly,
    AuthCorrupt,
    AuthLoaded,
    JournalCorrupt,
    JournalLoaded,
    auth_file_lock,
    delete_journal,
    load_auth,
    load_journal,
    pending_rotation_path,
    stored_auth,
    write_journal,
)
from enji_guard_cli.transport import EnjiHttpError, EnjiHttpResponse


@pytest.mark.parametrize(
    ("state", "event", "expected_state", "expected_effects"),
    [
        (Ready("r1"), Begin("r1"), Reserved("r1"), (PersistJournal(Reserved("r1")),)),
        (Ready("r1"), Imported("r2"), Ready("r2"), ()),
        (
            Reserved("r1"),
            DispatchBegun(),
            Requested("r1"),
            (PersistJournal(Requested("r1")), DispatchExchange("r1")),
        ),
        (
            Requested("r1"),
            ExchangeSucceeded("access_token=new; refresh_token=new", "r2"),
            Rotated("r1", "access_token=new; refresh_token=new", "r2"),
            (
                PersistJournal(Rotated("r1", "access_token=new; refresh_token=new", "r2")),
                PersistReplacement("r1", "access_token=new; refresh_token=new", "r2"),
            ),
        ),
        (
            Requested("r1"),
            ExchangeRejected("invalid"),
            Rejected("r1", "invalid"),
            (PersistJournal(Rejected("r1", "invalid")),),
        ),
        (
            Requested("r1"),
            ExchangeOutcomeUnknown("timeout"),
            OutcomeUnknown("r1", "timeout"),
            (PersistJournal(OutcomeUnknown("r1", "timeout")),),
        ),
        (Reserved("r1"), Recover(), Ready("r1"), (DeleteJournal(),)),
        (Reserved("r1"), Imported("r2"), Ready("r2"), (DeleteJournal(),)),
        (
            Requested("r1"),
            Recover(),
            OutcomeUnknown("r1", "interrupted after refresh dispatch"),
            (PersistJournal(OutcomeUnknown("r1", "interrupted after refresh dispatch")),),
        ),
        (Requested("r1"), Imported("r2"), Ready("r2"), (DeleteJournal(),)),
        (
            Rotated("r1", "access_token=new; refresh_token=new", "r2"),
            Recover(),
            Rotated("r1", "access_token=new; refresh_token=new", "r2"),
            (PersistReplacement("r1", "access_token=new; refresh_token=new", "r2"),),
        ),
        (Rotated("r1", "access_token=new; refresh_token=new", "r2"), Imported("r3"), Ready("r3"), (DeleteJournal(),)),
        (
            OutcomeUnknown("r1", "timeout"),
            Recover(),
            OutcomeUnknown("r1", "timeout"),
            (WaitForTerminalRevision("r1"),),
        ),
        (Rejected("r1", "invalid"), Imported("r2"), Ready("r2"), (DeleteJournal(),)),
    ],
)
def test_transition_matrix(
    state: RotationState,
    event: RotationEvent,
    expected_state: RotationState,
    expected_effects: tuple[Effect, ...],
) -> None:
    result = transition(state, event)

    assert result.state == expected_state
    assert result.effects == expected_effects


def test_transition_rejects_impossible_internal_event() -> None:
    with pytest.raises(InvalidTransitionError):
        transition(Ready("r1"), DispatchBegun())


def test_storage_validation_rejects_corrupt_external_inputs(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"version": 2, "revision": 1}', encoding="utf-8")
    pending_rotation_path(auth_file).write_text(
        json.dumps(
            {
                "version": 2,
                "source_revision": "r1",
                "state": "REQUESTED",
                "replacement_cookie_header": "should-not-be-here",
                "reason": None,
            }
        ),
        encoding="utf-8",
    )

    assert isinstance(load_auth(auth_file), AuthCorrupt)
    assert isinstance(load_journal(auth_file), JournalCorrupt)


def test_identical_imports_receive_distinct_revisions() -> None:
    first = stored_auth("https://fleet.enji.ai", {"type": "cookie", "cookie_header": "access=a; refresh=b"})
    second = stored_auth("https://fleet.enji.ai", {"type": "cookie", "cookie_header": "access=a; refresh=b"})

    assert first["revision"] != second["revision"]


@pytest.mark.parametrize("imported_at", ["not-a-timestamp", "2026-07-03T12:00:00", "2026-07-03T12:00:00+01:00"])
def test_storage_rejects_malformed_or_non_utc_imported_at(tmp_path: Path, imported_at: str) -> None:
    auth_file = tmp_path / "auth.json"
    auth = stored_auth("https://fleet.enji.ai", {"type": "cookie", "cookie_header": "access=a; refresh=b"})
    auth["imported_at"] = imported_at
    auth_file.write_text(json.dumps(auth), encoding="utf-8")

    result = load_auth(auth_file, now=datetime(2026, 7, 3, 12, 0, tzinfo=UTC))

    assert result == AuthCorrupt("credential imported_at must be an ISO 8601 UTC timestamp")


def test_storage_classifies_future_imported_at_stably(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    auth = stored_auth("https://fleet.enji.ai", {"type": "cookie", "cookie_header": "access=a; refresh=b"})
    auth["imported_at"] = (now + IMPORTED_AT_FUTURE_TOLERANCE + timedelta(microseconds=1)).isoformat()
    auth_file.write_text(json.dumps(auth), encoding="utf-8")

    result = load_auth(auth_file, now=now)

    assert result == AuthClockAnomaly("imported_at")


def test_storage_accepts_imported_at_at_future_tolerance_boundary(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    auth = stored_auth("https://fleet.enji.ai", {"type": "cookie", "cookie_header": "access=a; refresh=b"})
    auth["imported_at"] = (now + IMPORTED_AT_FUTURE_TOLERANCE).isoformat()
    auth_file.write_text(json.dumps(auth), encoding="utf-8")

    assert isinstance(load_auth(auth_file, now=now), AuthLoaded)


def test_ambiguous_response_is_dispatched_once_per_source_revision(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)

    class Exchange:
        calls = 0

        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            _ = source
            self.calls += 1
            return EnjiHttpResponse(status_code=502, headers={}, content=b"gateway unavailable")

    exchange = Exchange()
    coordinator = RefreshCoordinator(auth_file, exchange, terminal_wait_seconds=0)

    with pytest.raises(EnjiHttpError, match="outcome is unknown"):
        asyncio.run(coordinator.refresh(loaded.auth))
    with pytest.raises(EnjiHttpError, match="outcome is terminal"):
        asyncio.run(coordinator.refresh(loaded.auth))

    assert exchange.calls == 1
    journal = load_journal(auth_file)
    assert isinstance(journal, JournalLoaded)
    assert journal.state == OutcomeUnknown(loaded.auth["revision"], "ambiguous refresh response HTTP 502")


def test_malformed_success_cookie_response_is_terminal_and_never_replayed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)
    malformed_header = "refresh_token=malformed"
    original_load = SimpleCookie.load

    def reject_malformed_header(cookie: SimpleCookie, rawdata: str) -> None:
        if rawdata == malformed_header:
            raise CookieError("malformed Set-Cookie")
        original_load(cookie, rawdata)

    monkeypatch.setattr(cookies_module.SimpleCookie, "load", reject_malformed_header)
    dispatches = 0

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            nonlocal dispatches
            dispatches += 1
            return EnjiHttpResponse(
                status_code=200,
                headers={},
                content=b"{}",
                set_cookie_headers=("access_token=new", malformed_header),
            )

    exchange = Exchange()
    coordinator = RefreshCoordinator(auth_file, exchange, terminal_wait_seconds=0)
    with pytest.raises(EnjiHttpError, match="outcome is unknown"):
        asyncio.run(coordinator.refresh(loaded.auth))

    journal = load_journal(auth_file)
    assert isinstance(journal, JournalLoaded)
    assert journal.state == OutcomeUnknown(loaded.auth["revision"], "ambiguous refresh response HTTP 200")

    with pytest.raises(EnjiHttpError, match="outcome is terminal"):
        asyncio.run(RefreshCoordinator(auth_file, exchange, terminal_wait_seconds=0).refresh())
    asyncio.run(RefreshCoordinator(auth_file, exchange).recover_startup())

    assert dispatches == 1
    restarted_journal = load_journal(auth_file)
    assert isinstance(restarted_journal, JournalLoaded)
    assert isinstance(restarted_journal.state, OutcomeUnknown)


def test_success_cookie_parsing_keeps_separate_expires_comma_header(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            return EnjiHttpResponse(
                status_code=200,
                headers={},
                content=b"{}",
                set_cookie_headers=(
                    "access_token=new; Expires=Wed, 21 Oct 2037 07:28:00 GMT; Path=/",
                    "refresh_token=new; Path=/api/v1/auth",
                ),
            )

    rotated = asyncio.run(RefreshCoordinator(auth_file, Exchange()).refresh(loaded.auth))

    assert rotated["credential"] == {"type": "cookie", "cookie_header": "access_token=new; refresh_token=new"}


@pytest.mark.parametrize(
    "set_cookie_headers",
    [
        ("access_token=new",),
        ("access_token=new", "refresh_token=; Max-Age=0"),
    ],
)
def test_incomplete_or_deleting_success_cookies_are_terminal_unknown(
    tmp_path: Path, set_cookie_headers: tuple[str, ...]
) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            return EnjiHttpResponse(status_code=200, headers={}, content=b"{}", set_cookie_headers=set_cookie_headers)

    with pytest.raises(EnjiHttpError, match="outcome is unknown"):
        asyncio.run(RefreshCoordinator(auth_file, Exchange()).refresh(loaded.auth))

    journal = load_journal(auth_file)
    assert isinstance(journal, JournalLoaded)
    assert isinstance(journal.state, OutcomeUnknown)


def test_rotation_telemetry_is_terminal_once_and_redacts_sentinels(tmp_path: Path) -> None:
    auth_file = tmp_path / "SENTINEL_AUTH_PATH.json"
    secret = "SENTINEL_SECRET_COOKIE"
    import_cookie(f"access_token={secret}; refresh_token={secret}", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)
    events: list[tuple[str, dict[str, object]]] = []

    def sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> bool:
        _ = logger, level
        events.append((event, dict(fields)))
        return True

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            return EnjiHttpResponse(
                status_code=200,
                headers={},
                content=b"{}",
                set_cookie_headers=("access_token=new", "refresh_token=new"),
            )

    asyncio.run(
        RefreshCoordinator(auth_file, Exchange(), dependencies=CoordinatorDependencies(outcome_sink=sink)).refresh(
            loaded.auth
        )
    )

    assert events == [("enji_auth_rotation_rotated", {"event_key": f"auth-rotation:{loaded.auth['revision']}:rotated"})]
    assert secret not in repr(events)
    assert "SENTINEL_AUTH_PATH" not in repr(events)


def test_terminal_journal_redelivers_outcome_without_replaying_or_leaking(tmp_path: Path) -> None:
    auth_file = tmp_path / "SENTINEL_AUTH_PATH.json"
    import_cookie("access_token=SENTINEL_SECRET; refresh_token=SENTINEL_SECRET", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)
    write_journal(auth_file, OutcomeUnknown(loaded.auth["revision"], "SENTINEL_ERROR_MESSAGE"))
    events: list[tuple[str, dict[str, object]]] = []

    def sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> bool:
        _ = logger, level
        events.append((event, dict(fields)))
        return True

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            raise AssertionError("terminal journal must not dispatch")

    coordinator = RefreshCoordinator(
        auth_file, Exchange(), terminal_wait_seconds=0, dependencies=CoordinatorDependencies(outcome_sink=sink)
    )
    with pytest.raises(EnjiHttpError, match="import a fresh browser credential"):
        asyncio.run(coordinator.refresh(loaded.auth))

    assert events == [
        (
            "enji_auth_rotation_outcome_unknown",
            {"event_key": f"auth-rotation:{loaded.auth['revision']}:outcome_unknown"},
        )
    ]
    rendered = repr(events)
    assert "SENTINEL_SECRET" not in rendered
    assert "SENTINEL_AUTH_PATH" not in rendered
    assert "SENTINEL_ERROR_MESSAGE" not in rendered


def test_explicit_import_supersedes_terminal_journal(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)
    write_journal(auth_file, OutcomeUnknown(loaded.auth["revision"], "timeout"))

    replacement = stored_auth(
        loaded.auth["base_url"],
        {"type": "cookie", "cookie_header": "access_token=new; refresh_token=new"},
    )
    imported = import_credential(auth_file, replacement)

    assert imported["revision"] == replacement["revision"]
    assert not pending_rotation_path(auth_file).exists()


@pytest.mark.parametrize("target_operation", ["lock_open", "lock", "unlock"])
def test_auth_lock_exposes_each_durable_lock_boundary(tmp_path: Path, target_operation: str) -> None:
    auth_file = tmp_path / "auth.json"

    def failpoint(operation: str) -> None:
        if operation == target_operation:
            raise OSError(f"injected {target_operation}")

    with pytest.raises(OSError, match=f"injected {target_operation}"), auth_file_lock(auth_file, failpoint=failpoint):
        pass


@pytest.mark.parametrize("target_operation", ["unlink", "parent_directory_open", "parent_directory_fsync"])
def test_journal_delete_exposes_each_durable_removal_boundary(tmp_path: Path, target_operation: str) -> None:
    auth_file = tmp_path / "auth.json"
    auth = stored_auth("https://fleet.enji.ai", {"type": "cookie", "cookie_header": "access=a; refresh=b"})
    write_journal(auth_file, Reserved(auth["revision"]))

    def failpoint(operation: str) -> None:
        if operation == target_operation:
            raise OSError(f"injected {target_operation}")

    with pytest.raises(OSError, match=f"injected {target_operation}"):
        delete_journal(auth_file, failpoint=failpoint)


@pytest.mark.parametrize(
    "target_operation",
    [
        "before_write_journal",
        "temporary_file",
        "write",
        "file_fsync",
        "rename",
        "parent_directory_open",
        "parent_directory_fsync",
    ],
)
def test_failure_before_durable_requested_sends_zero_posts(tmp_path: Path, target_operation: str) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    calls = 0

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            nonlocal calls
            calls += 1
            raise AssertionError("request must not dispatch before REQUESTED is durable")

    def failpoint(operation: str) -> None:
        if operation == target_operation:
            raise OSError(f"injected {target_operation}")

    coordinator = RefreshCoordinator(
        auth_file, Exchange(), dependencies=CoordinatorDependencies(storage_failpoint=failpoint)
    )

    with pytest.raises(OSError, match=f"injected {target_operation}"):
        asyncio.run(coordinator.refresh())
    assert calls == 0


def test_post_dispatch_persistence_failure_is_terminal_and_never_replayed(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    dispatches = 0
    journal_writes = 0

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            nonlocal dispatches
            dispatches += 1
            return EnjiHttpResponse(
                status_code=200,
                headers={},
                content=b"{}",
                set_cookie_headers=("access_token=new", "refresh_token=new"),
            )

    def failpoint(operation: str) -> None:
        nonlocal journal_writes
        if operation == "before_write_journal":
            journal_writes += 1
            if journal_writes == 3:
                raise OSError("injected post-dispatch journal failure")

    exchange = Exchange()
    coordinator = RefreshCoordinator(
        auth_file, exchange, terminal_wait_seconds=0, dependencies=CoordinatorDependencies(storage_failpoint=failpoint)
    )

    with pytest.raises(TerminalRevisionRequiredError, match="import a fresh browser credential"):
        asyncio.run(coordinator.refresh())
    with pytest.raises(EnjiHttpError, match="outcome is terminal"):
        asyncio.run(RefreshCoordinator(auth_file, exchange, terminal_wait_seconds=0).refresh())

    assert dispatches == 1
    journal = load_journal(auth_file)
    assert isinstance(journal, JournalLoaded)
    assert isinstance(journal.state, Requested)


def test_rotated_successor_recovery_is_idempotent(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    before = load_auth(auth_file)
    assert isinstance(before, AuthLoaded)
    rotated_state = Rotated(before.auth["revision"], "access_token=new; refresh_token=new", "persisted-successor")
    write_journal(auth_file, rotated_state)

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            raise AssertionError("recovery must not dispatch")

    def accepting_sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> bool:
        _ = logger, level, event, fields
        return True

    coordinator = RefreshCoordinator(
        auth_file, Exchange(), dependencies=CoordinatorDependencies(outcome_sink=accepting_sink)
    )
    first = asyncio.run(coordinator.recover_startup())
    second = asyncio.run(coordinator.recover_startup())

    assert first is not None
    assert second == first
    assert first["revision"] == rotated_state.successor_revision
    assert first["credential"] == {"type": "cookie", "cookie_header": "access_token=new; refresh_token=new"}
    assert not pending_rotation_path(auth_file).exists()


def test_rotated_outbox_retries_same_key_after_sink_failure_without_another_post(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)
    delivery_keys: list[str] = []

    def rejecting_sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> bool:
        _ = logger, level
        assert event == "enji_auth_rotation_rotated"
        delivery_keys.append(str(fields["event_key"]))
        return False

    class Exchange:
        calls = 0

        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            self.calls += 1
            return EnjiHttpResponse(
                status_code=200,
                headers={},
                content=b"{}",
                set_cookie_headers=("access_token=new", "refresh_token=new"),
            )

    exchange = Exchange()
    rotated = asyncio.run(
        RefreshCoordinator(
            auth_file, exchange, dependencies=CoordinatorDependencies(outcome_sink=rejecting_sink)
        ).refresh(loaded.auth)
    )

    assert exchange.calls == 1
    journal = load_journal(auth_file)
    assert isinstance(journal, JournalLoaded)
    assert isinstance(journal.state, Rotated)
    assert rotated["revision"] == journal.state.successor_revision

    def accepting_sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> bool:
        _ = logger, level, event
        delivery_keys.append(str(fields["event_key"]))
        return True

    recovered = asyncio.run(
        RefreshCoordinator(
            auth_file, exchange, dependencies=CoordinatorDependencies(outcome_sink=accepting_sink)
        ).recover_startup()
    )

    assert recovered == rotated
    assert exchange.calls == 1
    assert delivery_keys == [f"auth-rotation:{loaded.auth['revision']}:rotated"] * 2
    assert not pending_rotation_path(auth_file).exists()


def test_terminal_outbox_survives_sink_failure_and_replays_after_restart(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)
    write_journal(auth_file, OutcomeUnknown(loaded.auth["revision"], "request timed out"))
    delivery_keys: list[str] = []

    def failing_sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> bool:
        _ = logger, level, event
        delivery_keys.append(str(fields["event_key"]))
        return False

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            raise AssertionError("terminal outcome must not be replayed")

    assert (
        asyncio.run(
            RefreshCoordinator(
                auth_file, Exchange(), dependencies=CoordinatorDependencies(outcome_sink=failing_sink)
            ).recover_startup()
        )
        == loaded.auth
    )
    assert pending_rotation_path(auth_file).exists()

    def accepting_sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> bool:
        _ = logger, level
        assert event == "enji_auth_rotation_outcome_unknown"
        delivery_keys.append(str(fields["event_key"]))
        return True

    coordinator = RefreshCoordinator(
        auth_file,
        Exchange(),
        terminal_wait_seconds=0,
        dependencies=CoordinatorDependencies(outcome_sink=accepting_sink),
    )
    with pytest.raises(EnjiHttpError, match="outcome is terminal"):
        asyncio.run(coordinator.refresh(loaded.auth))

    assert delivery_keys == [f"auth-rotation:{loaded.auth['revision']}:outcome_unknown"] * 2
    assert pending_rotation_path(auth_file).exists()

    import_credential(
        auth_file,
        stored_auth(
            loaded.auth["base_url"], {"type": "cookie", "cookie_header": "access_token=fresh; refresh_token=fresh"}
        ),
    )
    assert not pending_rotation_path(auth_file).exists()


@pytest.mark.parametrize(
    ("response", "expected_type"),
    [
        (
            EnjiHttpResponse(
                status_code=401,
                headers={},
                content=b'{"error":{"code":"AUTH_INVALID"}}',
            ),
            Rejected,
        ),
        (EnjiHttpResponse(status_code=401, headers={}, content=b"<html>proxy</html>"), OutcomeUnknown),
    ],
)
def test_refresh_rejection_requires_confirmed_enji_protocol_response(
    tmp_path: Path, response: EnjiHttpResponse, expected_type: type[Rejected | OutcomeUnknown]
) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            return response

    with pytest.raises(EnjiHttpError):
        asyncio.run(RefreshCoordinator(auth_file, Exchange()).refresh(loaded.auth))

    journal = load_journal(auth_file)
    assert isinstance(journal, JournalLoaded)
    assert isinstance(journal.state, expected_type)


def test_cancelled_exchange_becomes_terminal_unknown_and_is_not_replayed(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)

    async def exercise() -> None:
        started = asyncio.Event()

        class Exchange:
            calls = 0

            async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
                del source
                self.calls += 1
                started.set()
                await asyncio.Event().wait()
                raise AssertionError("cancelled exchange must not resume")

        exchange = Exchange()
        task = asyncio.create_task(RefreshCoordinator(auth_file, exchange).refresh(loaded.auth))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exchange.calls == 1

    asyncio.run(exercise())
    journal = load_journal(auth_file)
    assert isinstance(journal, JournalLoaded)
    assert isinstance(journal.state, OutcomeUnknown)

    class NoReplayExchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            raise AssertionError("terminal cancellation must not dispatch again")

    with pytest.raises(EnjiHttpError, match="outcome is terminal"):
        asyncio.run(RefreshCoordinator(auth_file, NoReplayExchange(), terminal_wait_seconds=0).refresh(loaded.auth))


def test_transport_failure_is_terminal_unknown_and_does_not_dispatch_again(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)

    class Exchange:
        calls = 0

        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            self.calls += 1
            raise EnjiHttpError("NETWORK", "connection reset")

    exchange = Exchange()
    with pytest.raises(EnjiHttpError, match="outcome is unknown"):
        asyncio.run(RefreshCoordinator(auth_file, exchange).refresh(loaded.auth))
    with pytest.raises(EnjiHttpError, match="outcome is terminal"):
        asyncio.run(RefreshCoordinator(auth_file, exchange, terminal_wait_seconds=0).refresh(loaded.auth))

    assert exchange.calls == 1


def test_import_at_commit_boundary_supersedes_rotation_result(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=old", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        class Exchange:
            calls = 0

            async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
                del source
                self.calls += 1
                started.set()
                await release.wait()
                return EnjiHttpResponse(
                    status_code=200,
                    headers={},
                    content=b"{}",
                    set_cookie_headers=("access_token=rotated", "refresh_token=rotated"),
                )

        exchange = Exchange()
        refresh_task = asyncio.create_task(RefreshCoordinator(auth_file, exchange).refresh(loaded.auth))
        await started.wait()
        imported = import_credential(
            auth_file,
            stored_auth(
                loaded.auth["base_url"],
                {"type": "cookie", "cookie_header": "access_token=imported; refresh_token=imported"},
            ),
        )
        release.set()
        result = await refresh_task

        assert exchange.calls == 1
        assert result == imported

    asyncio.run(exercise())
    current = load_auth(auth_file)
    assert isinstance(current, AuthLoaded)
    assert current.auth["credential"] == {
        "type": "cookie",
        "cookie_header": "access_token=imported; refresh_token=imported",
    }
    assert not pending_rotation_path(auth_file).exists()
