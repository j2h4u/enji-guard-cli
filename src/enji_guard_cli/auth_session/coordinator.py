"""Single-owner, one-shot coordination for cookie refresh rotation."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.cookies import CookieError
from pathlib import Path
from typing import Literal, Protocol

from enji_guard_cli.auth_session.auth_protocol import (
    HTTP_AUTH_FAILURE_CODES,
    HTTP_OK,
    is_refresh_token_invalid_response,
)
from enji_guard_cli.auth_session.cookies import (
    cookie_value,
    jwt_expires_at,
    merge_set_cookie_headers,
    set_cookie_names,
)
from enji_guard_cli.auth_session.ports import AuthEventSink, AuthOutcomeSink
from enji_guard_cli.auth_session.state_machine import (
    Begin,
    DispatchBegun,
    ExchangeOutcomeUnknown,
    ExchangeRejected,
    ExchangeSucceeded,
    OutcomeUnknown,
    Ready,
    Rejected,
    Requested,
    Reserved,
    Rotated,
    SourceRevisionAlive,
    rotation_event_key,
    rotation_event_metadata,
    transition,
)
from enji_guard_cli.auth_session.store import (
    AuthAbsent,
    AuthClockAnomaly,
    AuthCorrupt,
    AuthIoFailure,
    AuthLoaded,
    AuthUnsupported,
    CasSuperseded,
    CasWritten,
    JournalCorrupt,
    JournalIoFailure,
    JournalLoaded,
    OutcomeOutboxCorrupt,
    OutcomeOutboxIoFailure,
    OutcomeOutboxLoaded,
    OutcomeOutboxRecord,
    StorageFailpoint,
    StoredAuth,
    acknowledge_outcome,
    auth_file_lock,
    cas_replace_cookie,
    delete_journal,
    enqueue_outcome,
    load_auth,
    load_journal,
    load_outbox,
    stored_auth,
    write_auth_file,
    write_journal,
)
from enji_guard_cli.transport import EnjiHttpError, EnjiHttpResponse

TERMINAL_POLL_SECONDS = 0.05
MAX_DIAGNOSTIC_HEADER_LENGTH = 256
RefreshObservationOutcome = Literal["rotated", "rejected", "outcome_unknown"]
_LOGGER = logging.getLogger(__name__)


class RefreshExchange(Protocol):
    """The only network seam: one refresh POST, with no retry policy here."""

    async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse: ...


class PreDispatchLocalError(OSError):
    """A local durable-state failure before ``REQUESTED`` is committed.

    The supervisor may retry this narrowly typed failure.  It deliberately
    excludes request, response, and post-dispatch persistence failures.
    """

    def __init__(self, cause: OSError | TimeoutError) -> None:
        super().__init__(str(cause))


class TerminalRevisionRequiredError(EnjiHttpError):
    """A dispatched or terminal generation can advance only by import."""

    def __init__(self, source_revision: str, *, message: str) -> None:
        super().__init__("AUTH_IMPORT_REQUIRED", message)
        self.source_revision = source_revision


@dataclass(frozen=True, slots=True)
class RetainedRefreshSuccessor:
    """A post-dispatch replacement cookie held only in process memory."""

    source_revision: str
    auth: StoredAuth = field(repr=False)

    def snapshot(self) -> RetainedRefreshSuccessor:
        return RetainedRefreshSuccessor(self.source_revision, deepcopy(self.auth))


class PostDispatchPersistenceError(TerminalRevisionRequiredError):
    """A refresh succeeded but local storage could not persist the successor."""

    def __init__(self, retained_successor: RetainedRefreshSuccessor, *, cause: OSError | TimeoutError) -> None:
        super().__init__(
            retained_successor.source_revision,
            message="refresh dispatch completed; persist retained replacement credential when storage recovers",
        )
        self.retained_successor = retained_successor.snapshot()
        self.__cause__ = cause


def _stored_auth_revision(auth_path: Path) -> str | None:
    loaded = load_auth(auth_path)
    if isinstance(loaded, AuthLoaded):
        return loaded.auth["revision"]
    return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CoordinatorDependencies:
    storage_failpoint: StorageFailpoint | None = None
    event_sink: AuthEventSink | None = None
    outcome_sink: AuthOutcomeSink | None = None
    monotonic_fn: Callable[[], float] = time.monotonic
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep
    revision_reader: Callable[[Path], str | None] = _stored_auth_revision
    now_fn: Callable[[], datetime] = _utc_now


@dataclass(frozen=True, slots=True)
class RetainedSuccessorProjected:
    auth: StoredAuth = field(repr=False)


@dataclass(frozen=True, slots=True)
class RetainedSuccessorSuperseded:
    current_revision: str | None


RetainedSuccessorProjection = RetainedSuccessorProjected | RetainedSuccessorSuperseded


@dataclass(frozen=True, slots=True)
class _Dispatch:
    auth: StoredAuth


@dataclass(frozen=True, slots=True)
class _ReturnAuth:
    auth: StoredAuth


@dataclass(frozen=True, slots=True)
class _WaitForRevision:
    source_revision: str


_Preparation = _Dispatch | _ReturnAuth | _WaitForRevision
_RecoveryPreparation = _ReturnAuth | _WaitForRevision | None


class RefreshCoordinator:
    """Coordinates one POST per source credential revision.

    The instance lock coordinates coroutines in this process.  ``flock``
    coordinates processes, but is only ever held by synchronous storage work
    executed through ``asyncio.to_thread``.
    """

    def __init__(
        self,
        auth_path: Path,
        exchange: RefreshExchange,
        *,
        terminal_wait_seconds: float = 1.0,
        dependencies: CoordinatorDependencies | None = None,
    ) -> None:
        self._auth_path = auth_path
        self._exchange = exchange
        self._terminal_wait_seconds = terminal_wait_seconds
        resolved_dependencies = dependencies or CoordinatorDependencies()
        self._storage_failpoint = resolved_dependencies.storage_failpoint
        self._event_sink = resolved_dependencies.event_sink
        self._outcome_sink = resolved_dependencies.outcome_sink
        self._monotonic_fn = resolved_dependencies.monotonic_fn
        self._sleep_fn = resolved_dependencies.sleep_fn
        self._revision_reader = resolved_dependencies.revision_reader
        self._now_fn = resolved_dependencies.now_fn
        self._lock = asyncio.Lock()

    async def refresh(self, expected: StoredAuth | None = None) -> StoredAuth:
        """Refresh once, never retrying a request after dispatch begins."""

        async with self._lock:
            try:
                prepared = await asyncio.to_thread(self._prepare, expected)
            except (OSError, TimeoutError) as exc:
                raise PreDispatchLocalError(exc) from exc
            if isinstance(prepared, _ReturnAuth):
                return prepared.auth
            if isinstance(prepared, _WaitForRevision):
                return await self.wait_for_terminal_revision(prepared.source_revision)

            return await self._dispatch_and_commit(prepared.auth)

    async def _dispatch_and_commit(self, auth: StoredAuth) -> StoredAuth:
        try:
            response = await self._exchange.exchange_once(auth)
        except asyncio.CancelledError:
            try:
                self._emit_refresh_observation(auth, None, None, outcome="outcome_unknown")
                await asyncio.to_thread(self._commit_unknown, auth["revision"], "refresh task cancelled")
            except EnjiHttpError as exc:
                if exc.code != "AUTH_IMPORT_REQUIRED":
                    raise
            raise
        except EnjiHttpError as exc:
            self._emit_refresh_observation(auth, None, None, outcome="outcome_unknown")
            return await asyncio.to_thread(
                self._commit_unknown,
                auth["revision"],
                f"transport failure: {exc.code}",
            )
        except (OSError, TimeoutError) as exc:
            self._emit_refresh_observation(auth, None, None, outcome="outcome_unknown")
            return await asyncio.to_thread(
                self._commit_unknown,
                auth["revision"],
                f"transport failure: {type(exc).__name__}",
            )
        try:
            return await asyncio.to_thread(self._commit_response, auth, response)
        except PostDispatchPersistenceError:
            raise
        except (OSError, TimeoutError) as exc:
            raise TerminalRevisionRequiredError(
                auth["revision"], message="refresh dispatch completed; import a fresh browser credential"
            ) from exc

    async def wait_for_terminal_revision(self, source_revision: str) -> StoredAuth:
        """Wait for import/success to change a revision; never dispatch here."""

        deadline = self._monotonic_fn() + self._terminal_wait_seconds
        while True:
            revision = await asyncio.to_thread(self._revision_reader, self._auth_path)
            if revision is not None and revision != source_revision:
                loaded = await asyncio.to_thread(load_auth, self._auth_path)
                if isinstance(loaded, AuthLoaded):
                    return loaded.auth
            remaining_seconds = deadline - self._monotonic_fn()
            if remaining_seconds <= 0:
                raise TerminalRevisionRequiredError(
                    source_revision,
                    message="refresh outcome is terminal; import a fresh browser credential",
                )
            await self._sleep_fn(min(TERMINAL_POLL_SECONDS, remaining_seconds))

    async def recover_startup(self) -> StoredAuth | None:
        """Mark abandoned dispatched work unknown without issuing a request."""

        return await asyncio.to_thread(self._recover_startup)

    def _prepare(self, expected: StoredAuth | None) -> _Preparation:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            loaded = load_auth(self._auth_path)
            current = _loaded_or_raise(loaded)
            credential = current["credential"]
            if credential["type"] != "cookie":
                raise EnjiHttpError("AUTH_REQUIRED", "stored credential is not cookie based")

            journal_preparation = self._recover_or_wait(current)
            if journal_preparation is not None:
                return journal_preparation
            self._drain_outbox()
            if expected is not None and current["revision"] != expected["revision"]:
                return _ReturnAuth(current)

            reserved = transition(Ready(current["revision"]), Begin(current["revision"])).state
            assert isinstance(reserved, Reserved)
            write_journal(self._auth_path, reserved, failpoint=self._storage_failpoint)
            requested_transition = transition(reserved, DispatchBegun())
            assert isinstance(requested_transition.state, Requested)
            write_journal(self._auth_path, requested_transition.state, failpoint=self._storage_failpoint)
            return _Dispatch(current)

    def _recover_or_wait(self, current: StoredAuth) -> _RecoveryPreparation:
        journal = load_journal(self._auth_path)
        if isinstance(journal, (JournalCorrupt, JournalIoFailure)):
            raise EnjiHttpError("STORAGE", _journal_error_message(journal))
        if not isinstance(journal, JournalLoaded):
            return None
        state = journal.state
        outbox_enqueued = journal.outbox_enqueued
        if isinstance(state, Ready):
            raise EnjiHttpError("STORAGE", "refresh journal contains an invalid ready state")
        if isinstance(state, Rotated) and state.successor_revision == current["revision"]:
            self._record_terminal_outcome(state, outbox_enqueued=outbox_enqueued)
            return _ReturnAuth(current)
        if state.source_revision != current["revision"]:
            self._discard_rotation_state(state, outbox_enqueued=outbox_enqueued)
            return None
        return self._recover_matching_state(state, outbox_enqueued=outbox_enqueued)

    def _recover_matching_state(
        self, state: Rotated | Reserved | Requested | Rejected | OutcomeUnknown, *, outbox_enqueued: bool
    ) -> _RecoveryPreparation:
        if isinstance(state, Rotated):
            return self._recover_rotated(state, outbox_enqueued=outbox_enqueued)
        if isinstance(state, Reserved):
            delete_journal(self._auth_path, failpoint=self._storage_failpoint)
            return None
        if isinstance(state, (Rejected, OutcomeUnknown)):
            self._record_terminal_outcome(state, outbox_enqueued=outbox_enqueued)
        return _WaitForRevision(state.source_revision)

    def _recover_rotated(self, state: Rotated, *, outbox_enqueued: bool) -> _ReturnAuth:
        recovered = cas_replace_cookie(
            self._auth_path,
            state.source_revision,
            state.replacement_cookie_header,
            successor_revision=state.successor_revision,
            failpoint=self._storage_failpoint,
        )
        if isinstance(recovered, CasWritten):
            self._record_terminal_outcome(state, outbox_enqueued=outbox_enqueued)
            return _ReturnAuth(recovered.auth)
        assert isinstance(recovered, CasSuperseded)
        return _ReturnAuth(_loaded_or_raise(load_auth(self._auth_path)))

    def _commit_response(self, source: StoredAuth, response: EnjiHttpResponse) -> StoredAuth:
        cookie_header = _successful_replacement(source, response)
        if cookie_header is not None:
            return self._commit_success(source, cookie_header, response)
        if _is_confirmed_refresh_rejection(response):
            self._emit_refresh_observation(source, response, None, outcome="rejected")
            return self._commit_rejected(source["revision"], f"refresh rejected with HTTP {response.status_code}")
        self._emit_refresh_observation(source, response, None, outcome="outcome_unknown")
        return self._commit_unknown(source["revision"], f"ambiguous refresh response HTTP {response.status_code}")

    def _emit_refresh_observation(
        self,
        source: StoredAuth,
        response: EnjiHttpResponse | None,
        replacement_cookie_header: str | None,
        *,
        outcome: RefreshObservationOutcome,
        successor_revision: str | None = None,
    ) -> None:
        """Emit best-effort safe context without coupling auth progress to telemetry."""

        if self._event_sink is None:
            return
        fields: dict[str, object] = {
            "outcome": outcome,
            "source_revision": source["revision"],
        }
        if successor_revision is not None:
            fields["successor_revision"] = successor_revision
        fields.update(
            _refresh_diagnostic_fields(
                source,
                response,
                replacement_cookie_header,
                now=self._now_fn(),
            )
        )
        try:
            self._event_sink(
                _LOGGER,
                logging.INFO,
                "enji_auth_refresh_observed",
                fields,
            )
        except OSError, RuntimeError, ValueError:
            return

    def _commit_success(self, source: StoredAuth, cookie_header: str, response: EnjiHttpResponse) -> StoredAuth:
        source_revision = source["revision"]
        state = Requested(source_revision)
        rotated_transition = transition(state, ExchangeSucceeded(cookie_header))
        assert isinstance(rotated_transition.state, Rotated)
        self._emit_refresh_observation(
            source,
            response,
            cookie_header,
            outcome="rotated",
            successor_revision=rotated_transition.state.successor_revision,
        )
        successor = stored_auth(
            source["base_url"],
            {"type": "cookie", "cookie_header": cookie_header},
            revision=rotated_transition.state.successor_revision,
        )
        retained = RetainedRefreshSuccessor(source_revision, successor)
        try:
            with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
                if _is_superseded(self._auth_path, source_revision):
                    return _loaded_or_raise(load_auth(self._auth_path))
                write_journal(self._auth_path, rotated_transition.state, failpoint=self._storage_failpoint)
                result = cas_replace_cookie(
                    self._auth_path,
                    source_revision,
                    cookie_header,
                    successor_revision=rotated_transition.state.successor_revision,
                    failpoint=self._storage_failpoint,
                )
                if isinstance(result, CasWritten):
                    self._record_terminal_outcome(rotated_transition.state, outbox_enqueued=False)
                    return result.auth
                return _loaded_or_raise(load_auth(self._auth_path))
        except (OSError, TimeoutError) as exc:
            raise PostDispatchPersistenceError(retained, cause=exc) from exc

    def project_retained_successor(
        self,
        retained_successor: RetainedRefreshSuccessor,
    ) -> RetainedSuccessorProjection:
        """CAS-project a retained post-dispatch successor after storage recovers."""

        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            loaded = load_auth(self._auth_path)
            projected_auth = _projected_auth_or_none(loaded, retained_successor)
            state = _retained_rotated_state(retained_successor)
            if projected_auth is not None:
                self._record_terminal_outcome(state, outbox_enqueued=False)
                return RetainedSuccessorProjected(projected_auth)
            if not isinstance(loaded, AuthLoaded) or loaded.auth["revision"] != retained_successor.source_revision:
                return RetainedSuccessorSuperseded(_current_revision(loaded))
            write_journal(self._auth_path, state, failpoint=self._storage_failpoint)
            result = cas_replace_cookie(
                self._auth_path,
                retained_successor.source_revision,
                state.replacement_cookie_header,
                successor_revision=state.successor_revision,
                failpoint=self._storage_failpoint,
            )
            if isinstance(result, CasWritten):
                self._record_terminal_outcome(state, outbox_enqueued=False)
                return RetainedSuccessorProjected(result.auth)
            return RetainedSuccessorSuperseded(result.current_revision)

    def _commit_rejected(self, source_revision: str, reason: str) -> StoredAuth:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            if not _is_superseded(self._auth_path, source_revision):
                state = transition(Requested(source_revision), ExchangeRejected(reason)).state
                assert isinstance(state, Rejected)
                write_journal(self._auth_path, state, failpoint=self._storage_failpoint)
                self._record_terminal_outcome(state, outbox_enqueued=False)
            else:
                return _loaded_or_raise(load_auth(self._auth_path))
        raise TerminalRevisionRequiredError(source_revision, message="stored refresh cookie is not authenticated")

    def _commit_unknown(self, source_revision: str, reason: str) -> StoredAuth:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            if not _is_superseded(self._auth_path, source_revision):
                state = transition(Requested(source_revision), ExchangeOutcomeUnknown(reason)).state
                assert isinstance(state, OutcomeUnknown)
                write_journal(self._auth_path, state, failpoint=self._storage_failpoint)
                self._record_terminal_outcome(state, outbox_enqueued=False)
            else:
                return _loaded_or_raise(load_auth(self._auth_path))
        raise TerminalRevisionRequiredError(
            source_revision, message="refresh outcome is unknown; import a fresh browser credential"
        )

    def _recover_startup(self) -> StoredAuth | None:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            loaded = load_auth(self._auth_path)
            if not isinstance(loaded, AuthLoaded):
                return None
            self._drain_outbox()
            return self._recover_startup_journal(loaded.auth)

    def _recover_startup_journal(self, current: StoredAuth) -> StoredAuth:
        journal = load_journal(self._auth_path)
        result = current
        if isinstance(journal, JournalLoaded):
            state = journal.state
            outbox_enqueued = journal.outbox_enqueued
            if isinstance(state, Ready):
                raise EnjiHttpError("STORAGE", "refresh journal contains an invalid ready state")
            if isinstance(state, Rotated) and state.successor_revision == current["revision"]:
                self._record_terminal_outcome(state, outbox_enqueued=outbox_enqueued)
            elif state.source_revision != current["revision"]:
                self._discard_rotation_state(state, outbox_enqueued=outbox_enqueued)
            elif isinstance(state, Rotated):
                result = self._recover_rotated(state, outbox_enqueued=outbox_enqueued).auth
            elif isinstance(state, Reserved):
                delete_journal(self._auth_path, failpoint=self._storage_failpoint)
            elif isinstance(state, Requested):
                unknown = transition(state, ExchangeOutcomeUnknown("process exited after refresh dispatch")).state
                assert isinstance(unknown, OutcomeUnknown)
                write_journal(self._auth_path, unknown, failpoint=self._storage_failpoint)
                self._record_terminal_outcome(unknown, outbox_enqueued=False)
            else:
                assert isinstance(state, (Rejected, OutcomeUnknown))
                self._record_terminal_outcome(state, outbox_enqueued=outbox_enqueued)
        return result

    def _discard_rotation_state(
        self, state: Rotated | Reserved | Requested | Rejected | OutcomeUnknown, *, outbox_enqueued: bool
    ) -> None:
        """Clear obsolete coordination state after retaining any terminal outcome."""

        if isinstance(state, (Rotated, Rejected, OutcomeUnknown)) and not outbox_enqueued:
            enqueue_outcome(self._auth_path, _outbox_record(state), failpoint=self._storage_failpoint)
        delete_journal(self._auth_path, failpoint=self._storage_failpoint)

    def _record_terminal_outcome(self, state: Rotated | Rejected | OutcomeUnknown, *, outbox_enqueued: bool) -> None:
        """Make delivery independent from terminal generation coordination."""

        if not outbox_enqueued:
            enqueue_outcome(self._auth_path, _outbox_record(state), failpoint=self._storage_failpoint)
            write_journal(self._auth_path, state, outbox_enqueued=True, failpoint=self._storage_failpoint)
        self._drain_outbox()
        if isinstance(state, Rotated) and not self._outbox_contains(_outbox_record(state).event_key):
            delete_journal(self._auth_path, failpoint=self._storage_failpoint)

    def _drain_outbox(self) -> None:
        """Deliver every accepted durable record once per reconciliation pass."""

        outbox = load_outbox(self._auth_path)
        if isinstance(outbox, OutcomeOutboxCorrupt):
            raise EnjiHttpError("STORAGE", f"outcome outbox is corrupt: {outbox.detail}")
        if isinstance(outbox, OutcomeOutboxIoFailure):
            raise EnjiHttpError("STORAGE", f"{outbox.operation} failed: {outbox.error}")
        if not isinstance(outbox, OutcomeOutboxLoaded):
            return
        for record in outbox.records:
            if self._deliver_outbox_record(record):
                acknowledge_outcome(self._auth_path, record.event_key, failpoint=self._storage_failpoint)

    def _outbox_contains(self, event_key: str) -> bool:
        outbox = load_outbox(self._auth_path)
        if isinstance(outbox, OutcomeOutboxCorrupt):
            raise EnjiHttpError("STORAGE", f"outcome outbox is corrupt: {outbox.detail}")
        if isinstance(outbox, OutcomeOutboxIoFailure):
            raise EnjiHttpError("STORAGE", f"{outbox.operation} failed: {outbox.error}")
        return isinstance(outbox, OutcomeOutboxLoaded) and any(
            record.event_key == event_key for record in outbox.records
        )

    def _deliver_outbox_record(self, record: OutcomeOutboxRecord) -> bool:
        if self._outcome_sink is None:
            return False
        try:
            return (
                self._outcome_sink(
                    _LOGGER,
                    logging.INFO,
                    f"enji_auth_rotation_{record.outcome}",
                    {"event_key": record.event_key},
                )
                is True
            )
        except OSError, RuntimeError, ValueError:
            return False


def _retained_rotated_state(retained_successor: RetainedRefreshSuccessor) -> Rotated:
    credential = retained_successor.auth["credential"]
    if credential["type"] != "cookie":
        raise EnjiHttpError("AUTH_UNSUPPORTED", "retained refresh successor is not cookie based")
    return Rotated(
        retained_successor.source_revision,
        credential["cookie_header"],
        retained_successor.auth["revision"],
    )


def _projected_auth_or_none(loaded: object, retained_successor: RetainedRefreshSuccessor) -> StoredAuth | None:
    if isinstance(loaded, AuthLoaded) and loaded.auth["revision"] == retained_successor.auth["revision"]:
        return loaded.auth
    return None


def _current_revision(loaded: object) -> str | None:
    if isinstance(loaded, AuthLoaded):
        return loaded.auth["revision"]
    return None


def adjudicate_source_revision_alive(auth_path: Path, source_revision: str) -> bool:
    """Clear an ambiguous rotation once a probe proved the source still works.

    Returns whether the journal was cleared.  Everything is re-read under the
    lock, so a concurrent import or a rotation that landed while the probe was
    in flight wins and this call becomes a no-op.

    The earlier ``outcome_unknown`` outbox record is left standing -- that event
    really did happen -- and a second ``adjudicated_alive`` record is enqueued
    beside it.  Without the resolution record, telemetry would show a rotation
    that failed and never show it recovering, which reads as an outage nobody
    fixed.
    """

    with auth_file_lock(auth_path):
        loaded = load_auth(auth_path)
        if not isinstance(loaded, AuthLoaded) or loaded.auth["revision"] != source_revision:
            return False
        journal = load_journal(auth_path)
        if not isinstance(journal, JournalLoaded):
            return False
        state = journal.state
        if not isinstance(state, OutcomeUnknown) or state.source_revision != source_revision:
            return False
        resolved = transition(state, SourceRevisionAlive())
        assert isinstance(resolved.state, Ready)
        enqueue_outcome(auth_path, _adjudication_record(source_revision))
        delete_journal(auth_path)
        return True


def _adjudication_record(source_revision: str) -> OutcomeOutboxRecord:
    return OutcomeOutboxRecord("adjudicated_alive", rotation_event_key(source_revision, "adjudicated_alive"))


def import_credential(auth_path: Path, auth: StoredAuth) -> StoredAuth:
    """Supersede rotation coordination without erasing unacknowledged outcomes."""

    with auth_file_lock(auth_path):
        write_auth_file(auth_path, auth)
        journal = load_journal(auth_path)
        if (
            isinstance(journal, JournalLoaded)
            and isinstance(journal.state, (Rotated, Rejected, OutcomeUnknown))
            and not journal.outbox_enqueued
        ):
            enqueue_outcome(auth_path, _outbox_record(journal.state))
        delete_journal(auth_path)
    return auth


def _loaded_or_raise(loaded: object) -> StoredAuth:
    match loaded:
        case AuthLoaded(auth=auth):
            return auth
        case AuthAbsent():
            raise EnjiHttpError("AUTH_REQUIRED", "auth file does not exist")
        case AuthClockAnomaly():
            raise EnjiHttpError("AUTH_CLOCK_ANOMALY", "auth file imported_at is in the future")
        case AuthCorrupt(detail=detail):
            raise EnjiHttpError("AUTH_CORRUPT", f"auth file is corrupt: {detail}")
        case AuthUnsupported(version=version):
            raise EnjiHttpError("AUTH_UNSUPPORTED", f"auth file version is unsupported: {version!r}")
        case AuthIoFailure(operation=operation, error=error):
            raise EnjiHttpError("AUTH_IO_FAILURE", f"{operation} failed: {error}")
        case _:
            raise TypeError(f"unexpected auth load result: {type(loaded).__name__}")


def _outbox_record(state: Rotated | Rejected | OutcomeUnknown) -> OutcomeOutboxRecord:
    metadata = rotation_event_metadata(state)
    return OutcomeOutboxRecord(metadata.outcome, metadata.event_key)


def _journal_error_message(journal: JournalCorrupt | JournalIoFailure) -> str:
    if isinstance(journal, JournalCorrupt):
        return f"refresh journal is corrupt: {journal.detail}"
    return f"{journal.operation} failed: {journal.error}"


def _is_superseded(auth_path: Path, source_revision: str) -> bool:
    loaded = load_auth(auth_path)
    return not isinstance(loaded, AuthLoaded) or loaded.auth["revision"] != source_revision


def _successful_replacement(source: StoredAuth, response: EnjiHttpResponse) -> str | None:
    if response.status_code != HTTP_OK:
        return None
    credential = source["credential"]
    if credential["type"] != "cookie":
        return None
    try:
        names = set_cookie_names(response.set_cookie_headers)
        if not {"access_token", "refresh_token"}.issubset(names):
            return None
        return merge_set_cookie_headers(credential["cookie_header"], response.set_cookie_headers).value
    except CookieError, ValueError:
        # Once the refresh POST has returned, malformed cookie protocol data is
        # ambiguous: the server may already have consumed the one-time cookie.
        return None


def _refresh_diagnostic_fields(
    source: StoredAuth,
    response: EnjiHttpResponse | None,
    replacement_cookie_header: str | None,
    *,
    now: datetime,
) -> dict[str, object]:
    fields: dict[str, object] = {}
    credential = source["credential"]
    if credential["type"] != "cookie":
        return fields
    source_cookie_header = credential["cookie_header"]
    access_token = cookie_value(source_cookie_header, "access_token")
    access_expiry = jwt_expires_at(access_token) if access_token is not None else None
    if access_expiry is not None:
        fields["access_expires_in_seconds"] = int((access_expiry - now).total_seconds())

    if replacement_cookie_header is not None:
        source_refresh = cookie_value(source_cookie_header, "refresh_token")
        replacement_refresh = cookie_value(replacement_cookie_header, "refresh_token")
        if source_refresh is not None and replacement_refresh is not None:
            fields["refresh_token_changed"] = source_refresh != replacement_refresh

    headers: Mapping[str, str] = response.headers if response is not None else {}
    upstream_request_id = _bounded_header(headers, "x-request-id")
    if upstream_request_id is not None:
        fields["upstream_request_id"] = upstream_request_id
    cf_ray = _bounded_header(headers, "cf-ray")
    if cf_ray is not None:
        fields["cf_ray"] = cf_ray
    return fields


def _bounded_header(headers: Mapping[str, str], name: str) -> str | None:
    for raw_name, raw_value in headers.items():
        if raw_name.casefold() == name:
            value = raw_value.strip()
            return value[:MAX_DIAGNOSTIC_HEADER_LENGTH] if value else None
    return None


def _is_confirmed_refresh_rejection(response: EnjiHttpResponse) -> bool:
    return response.status_code in HTTP_AUTH_FAILURE_CODES and is_refresh_token_invalid_response(response)
