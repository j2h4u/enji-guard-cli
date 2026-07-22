import asyncio
import json
from pathlib import Path

import pytest

from enji_guard_cli.auth_session.api import import_cookie
from enji_guard_cli.auth_session.coordinator import RefreshCoordinator, import_credential
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

        async def exchange_once(self, _source: object) -> EnjiHttpResponse:
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
