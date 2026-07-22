import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path

import pytest

from enji_guard_cli.auth_session.api import import_cookie
from enji_guard_cli.auth_session.coordinator import RefreshCoordinator, import_credential
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
        (
            Reserved("r1"),
            DispatchBegun(),
            Requested("r1"),
            (PersistJournal(Requested("r1")), DispatchExchange("r1")),
        ),
        (
            Requested("r1"),
            ExchangeSucceeded("access_token=new; refresh_token=new"),
            Rotated("r1", "access_token=new; refresh_token=new"),
            (
                PersistJournal(Rotated("r1", "access_token=new; refresh_token=new")),
                PersistReplacement("r1", "access_token=new; refresh_token=new"),
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
        (
            Requested("r1"),
            Recover(),
            OutcomeUnknown("r1", "interrupted after refresh dispatch"),
            (PersistJournal(OutcomeUnknown("r1", "interrupted after refresh dispatch")),),
        ),
        (
            Rotated("r1", "access_token=new; refresh_token=new"),
            Recover(),
            Rotated("r1", "access_token=new; refresh_token=new"),
            (PersistReplacement("r1", "access_token=new; refresh_token=new"),),
        ),
        (
            OutcomeUnknown("r1", "timeout"),
            Recover(),
            OutcomeUnknown("r1", "timeout"),
            (WaitForTerminalRevision("r1"),),
        ),
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


def test_rotation_telemetry_is_terminal_once_and_redacts_sentinels(tmp_path: Path) -> None:
    auth_file = tmp_path / "SENTINEL_AUTH_PATH.json"
    secret = "SENTINEL_SECRET_COOKIE"
    import_cookie(f"access_token={secret}; refresh_token={secret}", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)
    events: list[tuple[str, dict[str, object]]] = []

    def sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> None:
        _ = logger, level
        events.append((event, dict(fields)))

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            return EnjiHttpResponse(
                status_code=200,
                headers={},
                content=b"{}",
                set_cookie_headers=("access_token=new", "refresh_token=new"),
            )

    asyncio.run(RefreshCoordinator(auth_file, Exchange(), event_sink=sink).refresh(loaded.auth))

    assert events == [("enji_auth_rotation_rotated", {})]
    assert secret not in repr(events)
    assert "SENTINEL_AUTH_PATH" not in repr(events)


def test_terminal_journal_emits_reimport_without_replaying_or_leaking(tmp_path: Path) -> None:
    auth_file = tmp_path / "SENTINEL_AUTH_PATH.json"
    import_cookie("access_token=SENTINEL_SECRET; refresh_token=SENTINEL_SECRET", auth_file)
    loaded = load_auth(auth_file)
    assert isinstance(loaded, AuthLoaded)
    write_journal(auth_file, OutcomeUnknown(loaded.auth["revision"], "SENTINEL_ERROR_MESSAGE"))
    events: list[tuple[str, dict[str, object]]] = []

    def sink(logger: logging.Logger, level: int, event: str, fields: Mapping[str, object]) -> None:
        _ = logger, level
        events.append((event, dict(fields)))

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            raise AssertionError("terminal journal must not dispatch")

    coordinator = RefreshCoordinator(auth_file, Exchange(), terminal_wait_seconds=0, event_sink=sink)
    with pytest.raises(EnjiHttpError, match="import a fresh browser credential"):
        asyncio.run(coordinator.refresh(loaded.auth))

    assert events == [("enji_auth_rotation_reimport_required", {})]
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

    coordinator = RefreshCoordinator(auth_file, Exchange(), storage_failpoint=failpoint)

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
    coordinator = RefreshCoordinator(auth_file, exchange, terminal_wait_seconds=0, storage_failpoint=failpoint)

    with pytest.raises(OSError, match="injected post-dispatch journal failure"):
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
    write_journal(auth_file, Rotated(before.auth["revision"], "access_token=new; refresh_token=new"))

    class Exchange:
        async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse:
            del source
            raise AssertionError("recovery must not dispatch")

    coordinator = RefreshCoordinator(auth_file, Exchange())
    first = asyncio.run(coordinator.recover_startup())
    second = asyncio.run(coordinator.recover_startup())

    assert first is not None
    assert second == first
    assert first["credential"] == {"type": "cookie", "cookie_header": "access_token=new; refresh_token=new"}
    assert not pending_rotation_path(auth_file).exists()
