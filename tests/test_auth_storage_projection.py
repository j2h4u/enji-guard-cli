import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from enji_guard_cli.auth_session.projection import (
    CredentialAbsent,
    CredentialClockAnomaly,
    CredentialCorrupt,
    CredentialIoFailure,
    CredentialReady,
    CredentialUnsupported,
    JournalCorruptProjection,
    JournalImpossibleState,
    JournalIoFailureProjection,
    ReimportRequired,
    RotationInProgress,
    RotationRecoveryAvailable,
    RotationReserved,
    project_auth,
)
from enji_guard_cli.auth_session.state_machine import (
    OutcomeUnknown,
    Ready,
    Rejected,
    Requested,
    Reserved,
    Rotated,
    RotationState,
    rotation_event_key,
    rotation_event_metadata,
)
from enji_guard_cli.auth_session.store import (
    AuthAbsent,
    AuthClockAnomaly,
    AuthCorrupt,
    AuthIoFailure,
    AuthLoaded,
    AuthLoadResult,
    AuthUnsupported,
    CasWritten,
    JournalAbsent,
    JournalCorrupt,
    JournalIoFailure,
    JournalLoaded,
    JournalLoadResult,
    StoredAuth,
    cas_replace_cookie,
    load_auth,
    load_journal,
    pending_rotation_path,
    stored_auth,
    write_auth_file,
    write_journal,
)


def _auth(revision: str = "source-revision") -> StoredAuth:
    return stored_auth(
        "https://fleet.enji.ai",
        {"type": "cookie", "cookie_header": "access_token=secret; refresh_token=secret"},
        revision=revision,
    )


@pytest.mark.parametrize(
    ("auth_result", "expected_type"),
    [
        (AuthAbsent(), CredentialAbsent),
        (AuthCorrupt("bad JSON"), CredentialCorrupt),
        (AuthUnsupported(3), CredentialUnsupported),
        (AuthIoFailure("read credential", OSError("denied")), CredentialIoFailure),
        (AuthClockAnomaly("imported_at"), CredentialClockAnomaly),
    ],
)
def test_projection_preserves_each_non_loaded_credential_classification(
    auth_result: AuthLoadResult, expected_type: type[object]
) -> None:
    assert isinstance(project_auth(auth_result, JournalAbsent()), expected_type)


@pytest.mark.parametrize(
    ("journal", "expected_type"),
    [
        (JournalAbsent(), CredentialReady),
        (JournalLoaded(Reserved("source-revision")), RotationReserved),
        (JournalLoaded(Requested("source-revision")), RotationInProgress),
        (JournalLoaded(Rotated("source-revision", "access=new", "successor-revision")), RotationRecoveryAvailable),
        (JournalLoaded(Rejected("source-revision", "invalid")), ReimportRequired),
        (JournalLoaded(OutcomeUnknown("source-revision", "timeout")), ReimportRequired),
        (JournalCorrupt("bad journal"), JournalCorruptProjection),
        (JournalIoFailure("read refresh journal", OSError("denied")), JournalIoFailureProjection),
        (JournalLoaded(Ready("source-revision")), JournalImpossibleState),
    ],
)
def test_projection_matrix_for_loaded_credential(journal: JournalLoadResult, expected_type: type[object]) -> None:
    assert isinstance(project_auth(AuthLoaded(_auth()), journal), expected_type)


def test_projection_treats_an_older_journal_as_superseded_ready_state() -> None:
    auth = _auth("current")
    result = project_auth(AuthLoaded(auth), JournalLoaded(OutcomeUnknown("old", "timeout")))

    assert result == CredentialReady(auth)


def test_auth_and_journal_loads_classify_absent_corrupt_unsupported_and_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = tmp_path / "auth.json"
    assert isinstance(load_auth(auth_path), AuthAbsent)
    auth_path.write_text("not-json", encoding="utf-8")
    assert isinstance(load_auth(auth_path), AuthCorrupt)
    auth_path.write_text(json.dumps({"version": 3}), encoding="utf-8")
    assert isinstance(load_auth(auth_path), AuthUnsupported)

    def fail_read(self: Path, *, encoding: str) -> str:
        del self, encoding
        raise OSError("injected read failure")

    monkeypatch.setattr(Path, "read_text", fail_read)
    assert isinstance(load_auth(auth_path), AuthIoFailure)
    assert isinstance(load_journal(auth_path), JournalIoFailure)


@pytest.mark.parametrize(
    "state",
    [
        Reserved("source-revision"),
        Requested("source-revision"),
        Rotated("source-revision", "access_token=SENTINEL", "successor-revision"),
        Rejected("source-revision", "invalid"),
        OutcomeUnknown("source-revision", "timeout"),
    ],
)
def test_journal_round_trips_only_valid_v2_state_combinations(tmp_path: Path, state: RotationState) -> None:
    auth_path = tmp_path / "auth.json"
    write_journal(auth_path, state)

    loaded = load_journal(auth_path)

    assert isinstance(loaded, JournalLoaded)
    assert loaded.state == state


@pytest.mark.parametrize(
    "override",
    [
        {"state": "REQUESTED", "replacement_cookie_header": "not-allowed"},
        {"state": "ROTATED", "successor_revision": None},
        {"state": "REJECTED", "outcome": "outcome_unknown"},
        {"state": "OUTCOME_UNKNOWN", "event_key": "auth-rotation:source-revision:rejected"},
    ],
)
def test_journal_rejects_impossible_schema_combinations(tmp_path: Path, override: dict[str, object]) -> None:
    auth_path = tmp_path / "auth.json"
    payload: dict[str, object] = {
        "version": 2,
        "source_revision": "source-revision",
        "state": "REQUESTED",
        "replacement_cookie_header": None,
        "reason": None,
        "successor_revision": None,
        "outcome": None,
        "event_key": None,
    }
    payload.update(override)
    pending_rotation_path(auth_path).write_text(json.dumps(payload), encoding="utf-8")

    assert isinstance(load_journal(auth_path), JournalCorrupt)


@pytest.mark.parametrize(
    "state",
    [
        Rotated("source-revision", "access_token=SENTINEL", "successor-revision"),
        Rejected("source-revision", "SENTINEL_REASON"),
        OutcomeUnknown("source-revision", "SENTINEL_REASON"),
    ],
)
def test_terminal_journal_event_key_is_stable_and_contains_no_credentials(
    tmp_path: Path, state: Rotated | Rejected | OutcomeUnknown
) -> None:
    auth_path = tmp_path / "auth.json"
    write_journal(auth_path, state)
    serialized = pending_rotation_path(auth_path).read_text(encoding="utf-8")
    metadata = rotation_event_metadata(state)

    assert f'"event_key": "{metadata.event_key}"' in serialized
    assert metadata.event_key == rotation_event_key("source-revision", metadata.outcome)
    assert "SENTINEL" not in metadata.event_key


def test_rotated_successor_revision_is_generated_before_persistence_and_used_by_cas(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    source = _auth()
    write_auth_file(auth_path, source)
    rotated = Rotated(source["revision"], "access_token=new; refresh_token=new", "successor-revision")
    write_journal(auth_path, rotated)

    result = cas_replace_cookie(
        auth_path,
        source["revision"],
        rotated.replacement_cookie_header,
        successor_revision=rotated.successor_revision,
    )

    assert isinstance(result, CasWritten)
    assert result.auth["revision"] == "successor-revision"
    assert (
        json.loads(pending_rotation_path(auth_path).read_text(encoding="utf-8"))["successor_revision"]
        == "successor-revision"
    )


@pytest.mark.parametrize(
    "operation",
    [
        "before_write_journal",
        "parent_directory_mkdir",
        "parent_directory_chmod",
        "temporary_file",
        "write",
        "file_fsync",
        "temporary_chmod",
        "rename",
        "destination_chmod",
        "parent_directory_open",
        "parent_directory_fsync",
        "parent_directory_close",
        "after_write_journal",
    ],
)
def test_journal_write_exposes_every_durable_failpoint(tmp_path: Path, operation: str) -> None:
    target_operation = operation

    def failpoint(operation: str) -> None:
        if operation == target_operation:
            raise OSError(f"injected {target_operation}")

    with pytest.raises(OSError, match=f"injected {target_operation}"):
        write_journal(tmp_path / "auth.json", Reserved("source-revision"), failpoint=failpoint)


def test_loaded_timestamp_at_current_time_is_not_an_anomaly(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    now = datetime(2026, 7, 23, tzinfo=UTC)
    auth = _auth()
    auth["imported_at"] = now.isoformat()
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    assert isinstance(load_auth(auth_path, now=now), AuthLoaded)
