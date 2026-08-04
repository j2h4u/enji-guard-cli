"""Single-owner execution boundary for one-shot cookie refresh rotation.

The coordinator turns the pure state machine's effects into storage and
network operations.  The boundary deliberately enforces these invariants:

* The ``REQUESTED`` journal is durable before the one-time refresh POST begins.
  No exception after that point authorizes an automatic replay.
* Only the coordinator dispatches refresh.  Status, readiness, gateway, and MCP
  paths are observers and cannot mutate or reconcile rotation state.
* Local failures before dispatch are narrowly retryable.  Failures after a
  successful exchange retain the successor in memory for later compare-and-swap
  projection instead of discarding it or sending another POST.
* Journal and credential writes are serialized by the process lock plus the
  auth-file lock.  A newer imported revision always wins over stale work.
* Terminal outcome telemetry uses a durable, idempotency-keyed outbox and is at
  least once.  Diagnostic telemetry is best effort and must never control auth.
* Credentials, cookie values, paths, and unbounded upstream bodies never enter
  terminal outcome events.

This is intentionally Enji-specific coordination, not a generic retry engine.
The reusable artifact is the invariant set and state-machine shape; transport,
storage, and protocol rejection rules remain adapters around this boundary.
"""

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from http.cookies import CookieError
from pathlib import Path
from typing import Literal, Protocol, cast

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
    AMBIGUITY_REPLAY_LIMIT,
    RECOVERY_SAFETY_POLICY,
    SAFE_RETRY_LIMIT,
    VALIDATION_RETRY_LIMIT,
    AuthorizeRecoveryDispatch,
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
    RotationAttempt,
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
HTTP_BAD_GATEWAY = 502
RefreshObservationOutcome = Literal["rotated", "rejected", "outcome_unknown"]
RecoveryContinuation = Literal["safe_retry", "validation_recovery", "ambiguity_replay"]
MAX_SAFE_RETRIES = SAFE_RETRY_LIMIT
MAX_VALIDATION_RECOVERIES = VALIDATION_RETRY_LIMIT
MAX_AMBIGUITY_REPLAYS = AMBIGUITY_REPLAY_LIMIT
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
PROC_START_TICKS_INDEX = 19
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RefreshOwner:
    boot_id: str
    pid: int
    start_ticks: str


def _process_start_ticks(pid: int) -> str | None:
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except FileNotFoundError, OSError, UnicodeDecodeError:
        return None
    remainder = stat.rpartition(")")[2].split()
    return remainder[PROC_START_TICKS_INDEX] if len(remainder) > PROC_START_TICKS_INDEX else None


def _current_owner() -> RefreshOwner:
    boot_id = BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    pid = os.getpid()
    start_ticks = _process_start_ticks(pid)
    if not boot_id or start_ticks is None:
        raise OSError("cannot establish local refresh ownership")
    return RefreshOwner(boot_id, pid, start_ticks)


def _owner_is_alive(owner: RefreshOwner) -> bool:
    try:
        boot_id = BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    except OSError, UnicodeDecodeError:
        return True
    return boot_id == owner.boot_id and _process_start_ticks(owner.pid) == owner.start_ticks


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


class RecoveryRequiredError(EnjiHttpError):
    """A durable, bounded recovery action remains for this source revision."""

    def __init__(
        self,
        source_revision: str,
        continuation: RecoveryContinuation,
        *,
        retry_after_seconds: float = 0.0,
    ) -> None:
        super().__init__("AUTH_RECOVERY_PENDING", "refresh recovery is pending")
        self.source_revision = source_revision
        self.continuation = continuation
        self.retry_after_seconds = max(0.0, retry_after_seconds)


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
    owner_fn: Callable[[], RefreshOwner] = _current_owner
    owner_alive_fn: Callable[[RefreshOwner], bool] = _owner_is_alive


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
_RecoveryPreparation = _Dispatch | _ReturnAuth | _WaitForRevision | None


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
        self._owner_fn = resolved_dependencies.owner_fn
        self._owner_alive_fn = resolved_dependencies.owner_alive_fn
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
                if exc.code not in {"AUTH_IMPORT_REQUIRED", "AUTH_RECOVERY_PENDING"}:
                    raise
            raise
        except EnjiHttpError as exc:
            self._emit_refresh_observation(auth, None, None, outcome="outcome_unknown")
            return await asyncio.to_thread(
                self._commit_unknown,
                auth["revision"],
                f"transport failure: {exc.code}",
                continuation="ambiguity_replay",
            )
        except (OSError, TimeoutError) as exc:
            self._emit_refresh_observation(auth, None, None, outcome="outcome_unknown")
            return await asyncio.to_thread(
                self._commit_unknown,
                auth["revision"],
                f"transport failure: {type(exc).__name__}",
                continuation="ambiguity_replay",
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

            return self._prepare_dispatch(
                current,
                RotationAttempt(
                    current["revision"],
                    "normal",
                    1,
                    recovery_deadline=self._recovery_deadline(),
                ),
            )

    def _recover_or_wait(self, current: StoredAuth) -> _RecoveryPreparation:
        journal = load_journal(self._auth_path)
        if isinstance(journal, (JournalCorrupt, JournalIoFailure)):
            raise EnjiHttpError("STORAGE", _journal_error_message(journal))
        if not isinstance(journal, JournalLoaded):
            return None
        state = journal.state
        outbox_enqueued = journal.outbox_enqueued
        attempt = journal.attempt
        if attempt is None:
            raise EnjiHttpError("STORAGE", "refresh journal is missing its recovery budget")
        if isinstance(state, Ready):
            raise EnjiHttpError("STORAGE", "refresh journal contains an invalid ready state")
        if isinstance(state, Rotated) and state.successor_revision == current["revision"]:
            self._record_terminal_outcome(state, outbox_enqueued=outbox_enqueued, attempt=attempt)
            return _ReturnAuth(current)
        if state.source_revision != current["revision"]:
            self._discard_rotation_state(state, outbox_enqueued=outbox_enqueued)
            return None
        return self._recover_matching_state(current, state, attempt=attempt, outbox_enqueued=outbox_enqueued)

    def _recover_matching_state(
        self,
        current: StoredAuth,
        state: Rotated | Reserved | Requested | Rejected | OutcomeUnknown,
        *,
        attempt: RotationAttempt,
        outbox_enqueued: bool,
    ) -> _RecoveryPreparation:
        match state:
            case Rotated():
                return self._recover_rotated(state, attempt=attempt, outbox_enqueued=outbox_enqueued)
            case Reserved():
                delete_journal(self._auth_path, failpoint=self._storage_failpoint)
                return None
            case Requested():
                return self._recover_requested(current, state, attempt)
            case OutcomeUnknown():
                self._record_terminal_outcome(state, attempt=attempt, outbox_enqueued=outbox_enqueued)
                return self._recover_unknown(current, state, attempt)
            case Rejected():
                self._record_terminal_outcome(state, attempt=attempt, outbox_enqueued=outbox_enqueued)
                return _WaitForRevision(state.source_revision)

    def _recover_requested(
        self, current: StoredAuth, state: Requested, attempt: RotationAttempt
    ) -> _RecoveryPreparation:
        owner = _attempt_owner(attempt)
        if owner is not None and self._owner_alive_fn(owner):
            return _WaitForRevision(state.source_revision)
        unknown = transition(state, ExchangeOutcomeUnknown("interrupted after refresh dispatch")).state
        assert isinstance(unknown, OutcomeUnknown)
        unknown_attempt = _with_continuation(attempt, "ambiguity_replay", self._recovery_deadline(), now=self._now_fn())
        write_journal(self._auth_path, unknown, attempt=unknown_attempt, failpoint=self._storage_failpoint)
        self._record_terminal_outcome(unknown, attempt=unknown_attempt, outbox_enqueued=False)
        return self._recover_unknown(current, unknown, unknown_attempt)

    def _recover_rotated(self, state: Rotated, *, attempt: RotationAttempt, outbox_enqueued: bool) -> _ReturnAuth:
        recovered = cas_replace_cookie(
            self._auth_path,
            state.source_revision,
            state.replacement_cookie_header,
            successor_revision=state.successor_revision,
            failpoint=self._storage_failpoint,
        )
        if isinstance(recovered, CasWritten):
            self._record_terminal_outcome(state, attempt=attempt, outbox_enqueued=outbox_enqueued)
            return _ReturnAuth(recovered.auth)
        assert isinstance(recovered, CasSuperseded)
        return _ReturnAuth(_loaded_or_raise(load_auth(self._auth_path)))

    def _prepare_dispatch(
        self,
        current: StoredAuth,
        attempt: RotationAttempt,
        *,
        recovery_state: OutcomeUnknown | None = None,
    ) -> _Dispatch:
        """Persist the next bounded dispatch before the exchange begins."""

        owner = self._owner_fn()
        owned_attempt = replace(
            attempt,
            continuation=None,
            owner_boot_id=owner.boot_id,
            owner_pid=owner.pid,
            owner_start_ticks=owner.start_ticks,
            next_attempt_at=None,
            stop_reason=None,
        )
        authorization = (
            transition(
                recovery_state,
                AuthorizeRecoveryDispatch(
                    cast(RecoveryContinuation, attempt.attempt_kind),
                    attempt.dispatch_count,
                    attempt.safe_retry_count,
                    attempt.validation_retry_count,
                    attempt.ambiguity_replay_count,
                    attempt.total_dispatch_cap,
                    not _deadline_passed(attempt.recovery_deadline, self._now_fn()),
                    _seconds_until(attempt.next_attempt_at, self._now_fn()) <= 0,
                ),
            )
            if recovery_state is not None
            else transition(Ready(current["revision"]), Begin(current["revision"]))
        )
        reserved = authorization.state
        assert isinstance(reserved, Reserved)
        write_journal(self._auth_path, reserved, attempt=owned_attempt, failpoint=self._storage_failpoint)
        requested_transition = transition(reserved, DispatchBegun())
        assert isinstance(requested_transition.state, Requested)
        write_journal(
            self._auth_path,
            requested_transition.state,
            attempt=owned_attempt,
            failpoint=self._storage_failpoint,
        )
        return _Dispatch(current)

    def _recover_unknown(
        self, current: StoredAuth, state: OutcomeUnknown, attempt: RotationAttempt
    ) -> _RecoveryPreparation:
        next_attempt = self._next_recovery_attempt(attempt)
        if next_attempt is None:
            stopped = replace(
                attempt,
                continuation="stop",
                owner_boot_id=None,
                owner_pid=None,
                owner_start_ticks=None,
                stop_reason=attempt.stop_reason or "recovery-budget-exhausted",
            )
            write_journal(self._auth_path, state, attempt=stopped, failpoint=self._storage_failpoint)
            return _WaitForRevision(state.source_revision)
        retry_after = _seconds_until(next_attempt.next_attempt_at, self._now_fn())
        if retry_after > 0:
            pending_continuation = cast(RecoveryContinuation, next_attempt.continuation)
            assert pending_continuation in {"safe_retry", "validation_recovery", "ambiguity_replay"}
            raise RecoveryRequiredError(
                state.source_revision,
                pending_continuation,
                retry_after_seconds=retry_after,
            )
        return self._prepare_dispatch(current, next_attempt, recovery_state=state)

    def _next_recovery_attempt(self, attempt: RotationAttempt) -> RotationAttempt | None:
        continuation = attempt.continuation
        if (
            continuation is None
            or continuation == "stop"
            or attempt.dispatch_count >= attempt.total_dispatch_cap
            or _deadline_passed(attempt.recovery_deadline, self._now_fn())
        ):
            return None
        if continuation == "safe_retry" and attempt.safe_retry_count < MAX_SAFE_RETRIES:
            return RotationAttempt(
                attempt.rotation_id,
                "safe_retry",
                attempt.dispatch_count + 1,
                attempt.safe_retry_count + 1,
                attempt.validation_retry_count,
                attempt.ambiguity_replay_count,
                attempt.recovery_deadline,
                continuation,
                next_attempt_at=attempt.next_attempt_at,
                total_dispatch_cap=attempt.total_dispatch_cap,
            )
        if continuation == "validation_recovery" and attempt.validation_retry_count < MAX_VALIDATION_RECOVERIES:
            return RotationAttempt(
                attempt.rotation_id,
                "validation_recovery",
                attempt.dispatch_count + 1,
                attempt.safe_retry_count,
                attempt.validation_retry_count + 1,
                attempt.ambiguity_replay_count,
                attempt.recovery_deadline,
                continuation,
                next_attempt_at=attempt.next_attempt_at,
                total_dispatch_cap=attempt.total_dispatch_cap,
            )
        if continuation == "ambiguity_replay" and attempt.ambiguity_replay_count < MAX_AMBIGUITY_REPLAYS:
            return RotationAttempt(
                attempt.rotation_id,
                "ambiguity_replay",
                attempt.dispatch_count + 1,
                attempt.safe_retry_count,
                attempt.validation_retry_count,
                attempt.ambiguity_replay_count + 1,
                attempt.recovery_deadline,
                continuation,
                next_attempt_at=attempt.next_attempt_at,
                total_dispatch_cap=attempt.total_dispatch_cap,
            )
        return None

    def _recovery_deadline(self) -> str:
        return (self._now_fn() + RECOVERY_SAFETY_POLICY.recovery_window).isoformat()

    def _commit_response(self, source: StoredAuth, response: EnjiHttpResponse) -> StoredAuth:
        cookie_header = _successful_replacement(source, response)
        if cookie_header is not None:
            return self._commit_success(source, cookie_header, response)
        continuation = _response_continuation(response)
        if continuation == "stop":
            self._emit_refresh_observation(source, response, None, outcome="rejected")
            return self._commit_rejected(source["revision"], f"refresh rejected with HTTP {response.status_code}")
        return self._commit_unknown(
            source["revision"],
            f"refresh response HTTP {response.status_code}",
            continuation=continuation,
            observation=(source, response),
        )

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
        journal = load_journal(self._auth_path)
        if isinstance(journal, JournalLoaded) and journal.attempt is not None:
            attempt = journal.attempt
            fields.update(
                {
                    "attempt_kind": attempt.attempt_kind,
                    "attempt_number": attempt.dispatch_count,
                    "dispatch_budget_remaining": max(0, attempt.total_dispatch_cap - attempt.dispatch_count),
                }
            )
            if attempt.next_attempt_at is not None:
                fields["next_attempt_at"] = attempt.next_attempt_at
            if attempt.recovery_deadline is not None:
                fields["recovery_deadline"] = attempt.recovery_deadline
            if attempt.stop_reason is not None:
                fields["stop_reason"] = attempt.stop_reason
        if response is not None:
            fields["response_class"] = _normalized_response_class(response)
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
                attempt = _without_owner(self._attempt_for_source(source_revision))
                write_journal(
                    self._auth_path, rotated_transition.state, attempt=attempt, failpoint=self._storage_failpoint
                )
                result = cas_replace_cookie(
                    self._auth_path,
                    source_revision,
                    cookie_header,
                    successor_revision=rotated_transition.state.successor_revision,
                    failpoint=self._storage_failpoint,
                )
                if isinstance(result, CasWritten):
                    self._record_terminal_outcome(rotated_transition.state, attempt=attempt, outbox_enqueued=False)
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
            attempt = _without_owner(self._attempt_for_source(retained_successor.source_revision))
            if projected_auth is not None:
                self._record_terminal_outcome(state, attempt=attempt, outbox_enqueued=False)
                return RetainedSuccessorProjected(projected_auth)
            if not isinstance(loaded, AuthLoaded) or loaded.auth["revision"] != retained_successor.source_revision:
                return RetainedSuccessorSuperseded(_current_revision(loaded))
            write_journal(self._auth_path, state, attempt=attempt, failpoint=self._storage_failpoint)
            result = cas_replace_cookie(
                self._auth_path,
                retained_successor.source_revision,
                state.replacement_cookie_header,
                successor_revision=state.successor_revision,
                failpoint=self._storage_failpoint,
            )
            if isinstance(result, CasWritten):
                self._record_terminal_outcome(state, attempt=attempt, outbox_enqueued=False)
                return RetainedSuccessorProjected(result.auth)
            return RetainedSuccessorSuperseded(result.current_revision)

    def _commit_rejected(self, source_revision: str, reason: str) -> StoredAuth:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            if not _is_superseded(self._auth_path, source_revision):
                state = transition(Requested(source_revision), ExchangeRejected(reason)).state
                assert isinstance(state, Rejected)
                attempt = _without_owner(self._attempt_for_source(source_revision))
                write_journal(self._auth_path, state, attempt=attempt, failpoint=self._storage_failpoint)
                self._record_terminal_outcome(state, attempt=attempt, outbox_enqueued=False)
            else:
                return _loaded_or_raise(load_auth(self._auth_path))
        raise TerminalRevisionRequiredError(source_revision, message="stored refresh cookie is not authenticated")

    def _commit_unknown(
        self,
        source_revision: str,
        reason: str,
        *,
        continuation: RecoveryContinuation = "ambiguity_replay",
        observation: tuple[StoredAuth, EnjiHttpResponse] | None = None,
    ) -> StoredAuth:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            if not _is_superseded(self._auth_path, source_revision):
                state = transition(Requested(source_revision), ExchangeOutcomeUnknown(reason)).state
                assert isinstance(state, OutcomeUnknown)
                attempt = _with_continuation(
                    self._attempt_for_source(source_revision),
                    continuation,
                    self._recovery_deadline(),
                    now=self._now_fn(),
                )
                response_class = _normalized_response_class(observation[1]) if observation is not None else None
                attempt = replace(attempt, response_class=response_class)
                write_journal(self._auth_path, state, attempt=attempt, failpoint=self._storage_failpoint)
                if observation is not None:
                    self._emit_refresh_observation(
                        observation[0],
                        observation[1],
                        None,
                        outcome="outcome_unknown",
                    )
                self._record_terminal_outcome(state, attempt=attempt, outbox_enqueued=False)
            else:
                return _loaded_or_raise(load_auth(self._auth_path))
        if attempt.continuation == "stop":
            raise TerminalRevisionRequiredError(
                source_revision, message="refresh recovery is exhausted; import a fresh browser credential"
            )
        raise RecoveryRequiredError(source_revision, continuation)

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
            attempt = journal.attempt
            if attempt is None:
                raise EnjiHttpError("STORAGE", "refresh journal is missing its recovery budget")
            if isinstance(state, Ready):
                raise EnjiHttpError("STORAGE", "refresh journal contains an invalid ready state")
            if isinstance(state, Rotated) and state.successor_revision == current["revision"]:
                self._record_terminal_outcome(state, attempt=attempt, outbox_enqueued=outbox_enqueued)
            elif state.source_revision != current["revision"]:
                self._discard_rotation_state(state, outbox_enqueued=outbox_enqueued)
            elif isinstance(state, Rotated):
                result = self._recover_rotated(state, attempt=attempt, outbox_enqueued=outbox_enqueued).auth
            elif isinstance(state, Reserved):
                delete_journal(self._auth_path, failpoint=self._storage_failpoint)
            elif isinstance(state, Requested):
                owner = _attempt_owner(attempt)
                if owner is not None and self._owner_alive_fn(owner):
                    return result
                unknown = transition(state, ExchangeOutcomeUnknown("process exited after refresh dispatch")).state
                assert isinstance(unknown, OutcomeUnknown)
                unknown_attempt = _with_continuation(
                    attempt, "ambiguity_replay", self._recovery_deadline(), now=self._now_fn()
                )
                write_journal(self._auth_path, unknown, attempt=unknown_attempt, failpoint=self._storage_failpoint)
                self._record_terminal_outcome(unknown, attempt=unknown_attempt, outbox_enqueued=False)
            else:
                assert isinstance(state, (Rejected, OutcomeUnknown))
                self._record_terminal_outcome(state, attempt=attempt, outbox_enqueued=outbox_enqueued)
        return result

    def _discard_rotation_state(
        self, state: Rotated | Reserved | Requested | Rejected | OutcomeUnknown, *, outbox_enqueued: bool
    ) -> None:
        """Clear obsolete coordination state after retaining any terminal outcome."""

        if isinstance(state, (Rotated, Rejected, OutcomeUnknown)) and not outbox_enqueued:
            enqueue_outcome(self._auth_path, _outbox_record(state), failpoint=self._storage_failpoint)
        delete_journal(self._auth_path, failpoint=self._storage_failpoint)

    def _record_terminal_outcome(
        self, state: Rotated | Rejected | OutcomeUnknown, *, attempt: RotationAttempt, outbox_enqueued: bool
    ) -> None:
        """Make delivery independent from terminal generation coordination."""

        if not outbox_enqueued:
            enqueue_outcome(self._auth_path, _outbox_record(state), failpoint=self._storage_failpoint)
            write_journal(
                self._auth_path,
                state,
                outbox_enqueued=True,
                attempt=attempt,
                failpoint=self._storage_failpoint,
            )
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

    def _attempt_for_source(self, source_revision: str) -> RotationAttempt:
        journal = load_journal(self._auth_path)
        if (
            isinstance(journal, JournalLoaded)
            and not isinstance(journal.state, Ready)
            and journal.state.source_revision == source_revision
            and journal.attempt is not None
        ):
            return journal.attempt
        return RotationAttempt(source_revision, "normal", 1)

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


def _response_continuation(response: EnjiHttpResponse) -> RecoveryContinuation | Literal["stop"]:
    """Classify only client-observable refresh responses into recovery lanes.

    A complete successor is handled before this function.  A structured
    invalid-refresh response is not treated as proof that renewal is lost: the
    observable response can also represent a transient validation failure.
    Other gateway-shaped outcomes disclose no useful phase evidence and get
    one durable ambiguity replay.  Explicitly terminal or post-consumption
    shaped responses never replay the old source revision.
    """

    message = _response_message(response)
    if response.status_code == HTTP_BAD_GATEWAY and message == "failed to refresh session":
        return "safe_retry"
    if _is_confirmed_refresh_rejection(response):
        return "validation_recovery"
    if response.status_code in HTTP_AUTH_FAILURE_CODES and message in {
        "session is no longer valid",
        "account is blocked",
    }:
        return "stop"
    return "ambiguity_replay"


def _response_message(response: EnjiHttpResponse) -> str | None:
    try:
        payload = response.json(operation="refresh response classification")
    except EnjiHttpError:
        return None
    mapping = _as_object_mapping(payload)
    if mapping is None:
        return None
    message = mapping.get("message")
    return message if isinstance(message, str) else None


def _as_object_mapping(value: object) -> Mapping[object, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[object, object], value)


def _normalized_response_class(response: EnjiHttpResponse) -> str:
    """Return a bounded label derived without retaining response content."""

    if response.status_code == HTTP_OK:
        return "success_or_incomplete_success"
    continuation = _response_continuation(response)
    return f"http_{response.status_code}_{continuation}"


def _with_continuation(
    attempt: RotationAttempt,
    continuation: RecoveryContinuation,
    deadline: str,
    *,
    now: datetime,
) -> RotationAttempt:
    parsed_deadline = attempt.recovery_deadline or deadline
    if not _continuation_available(attempt, continuation):
        return replace(
            attempt,
            recovery_deadline=parsed_deadline,
            continuation="stop",
            owner_boot_id=None,
            owner_pid=None,
            owner_start_ticks=None,
            next_attempt_at=None,
            stop_reason="recovery-budget-exhausted",
        )
    delay = {
        "safe_retry": RECOVERY_SAFETY_POLICY.safe_retry_delay,
        "validation_recovery": RECOVERY_SAFETY_POLICY.validation_retry_delay,
        "ambiguity_replay": RECOVERY_SAFETY_POLICY.ambiguity_replay_delay,
    }[continuation]
    next_attempt = now.astimezone(UTC) + delay
    parsed_absolute_deadline = _parse_deadline(parsed_deadline)
    if (
        parsed_absolute_deadline is None
        or now.astimezone(UTC) >= parsed_absolute_deadline
        or next_attempt > parsed_absolute_deadline
    ):
        return replace(
            attempt,
            recovery_deadline=parsed_deadline,
            continuation="stop",
            owner_boot_id=None,
            owner_pid=None,
            owner_start_ticks=None,
            next_attempt_at=None,
            stop_reason="recovery-deadline-exhausted",
        )
    return replace(
        attempt,
        recovery_deadline=parsed_deadline,
        continuation=continuation,
        owner_boot_id=None,
        owner_pid=None,
        owner_start_ticks=None,
        next_attempt_at=next_attempt.isoformat(),
        stop_reason=None,
    )


def _continuation_available(attempt: RotationAttempt, continuation: RecoveryContinuation) -> bool:
    if attempt.dispatch_count >= attempt.total_dispatch_cap:
        return False
    limits = {
        "safe_retry": (attempt.safe_retry_count, MAX_SAFE_RETRIES),
        "validation_recovery": (attempt.validation_retry_count, MAX_VALIDATION_RECOVERIES),
        "ambiguity_replay": (attempt.ambiguity_replay_count, MAX_AMBIGUITY_REPLAYS),
    }
    count, limit = limits[continuation]
    return count < limit


def _attempt_owner(attempt: RotationAttempt) -> RefreshOwner | None:
    """Return persisted ownership only when the complete identity is present."""

    if attempt.owner_boot_id is None or attempt.owner_pid is None or attempt.owner_start_ticks is None:
        return None
    return RefreshOwner(attempt.owner_boot_id, attempt.owner_pid, attempt.owner_start_ticks)


def _without_owner(attempt: RotationAttempt) -> RotationAttempt:
    return replace(
        attempt,
        owner_boot_id=None,
        owner_pid=None,
        owner_start_ticks=None,
    )


def _deadline_passed(deadline: str | None, now: datetime) -> bool:
    parsed = _parse_deadline(deadline)
    return parsed is None or now.astimezone(UTC) >= parsed


def _parse_deadline(deadline: str | None) -> datetime | None:
    if deadline is None:
        return None
    try:
        parsed = datetime.fromisoformat(deadline)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _seconds_until(timestamp: str | None, now: datetime) -> float:
    if timestamp is None:
        return 0.0
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        return 0.0
    return max(0.0, (parsed.astimezone(UTC) - now.astimezone(UTC)).total_seconds())
