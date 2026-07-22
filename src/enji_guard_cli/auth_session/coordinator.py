"""Single-owner, one-shot coordination for cookie refresh rotation."""

import asyncio
import logging
from dataclasses import dataclass
from http.cookies import CookieError
from pathlib import Path
from typing import Protocol

from enji_guard_cli.auth_session.cookies import merge_set_cookie_headers, set_cookie_names
from enji_guard_cli.auth_session.ports import AuthEventSink
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
    StorageFailpoint,
    StoredAuth,
    auth_file_lock,
    cas_replace_cookie,
    delete_journal,
    load_auth,
    load_journal,
    write_auth_file,
    write_journal,
)
from enji_guard_cli.transport import EnjiHttpError, EnjiHttpResponse

HTTP_OK = 200
TERMINAL_POLL_SECONDS = 0.05
_LOGGER = logging.getLogger(__name__)


class RefreshExchange(Protocol):
    """The only network seam: one refresh POST, with no retry policy here."""

    async def exchange_once(self, source: StoredAuth) -> EnjiHttpResponse: ...


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
        storage_failpoint: StorageFailpoint | None = None,
        event_sink: AuthEventSink | None = None,
    ) -> None:
        self._auth_path = auth_path
        self._exchange = exchange
        self._terminal_wait_seconds = terminal_wait_seconds
        self._storage_failpoint = storage_failpoint
        self._event_sink = event_sink
        self._lock = asyncio.Lock()

    async def refresh(self, expected: StoredAuth | None = None) -> StoredAuth:
        """Refresh once, never retrying a request after dispatch begins."""

        async with self._lock:
            try:
                prepared = await asyncio.to_thread(self._prepare, expected)
            except (OSError, TimeoutError) as exc:
                self._emit("retryable_pre_dispatch_failure", error_type=type(exc).__name__)
                raise
            except EnjiHttpError as exc:
                self._emit("invariant_failure", code=exc.code)
                raise
            if isinstance(prepared, _ReturnAuth):
                return prepared.auth
            if isinstance(prepared, _WaitForRevision):
                return await self.wait_for_terminal_revision(prepared.source_revision)

            try:
                response = await self._exchange.exchange_once(prepared.auth)
            except asyncio.CancelledError:
                await asyncio.to_thread(self._commit_unknown, prepared.auth["revision"], "refresh task cancelled")
                raise
            except EnjiHttpError as exc:
                return await asyncio.to_thread(
                    self._commit_unknown,
                    prepared.auth["revision"],
                    f"transport failure: {exc.code}",
                )
            except (OSError, TimeoutError) as exc:
                return await asyncio.to_thread(
                    self._commit_unknown,
                    prepared.auth["revision"],
                    f"transport failure: {type(exc).__name__}",
                )
            return await asyncio.to_thread(self._commit_response, prepared.auth, response)

    async def wait_for_terminal_revision(self, source_revision: str) -> StoredAuth:
        """Wait for import/success to change a revision; never dispatch here."""

        deadline = asyncio.get_running_loop().time() + self._terminal_wait_seconds
        while True:
            loaded = await asyncio.to_thread(load_auth, self._auth_path)
            if isinstance(loaded, AuthLoaded) and loaded.auth["revision"] != source_revision:
                return loaded.auth
            if asyncio.get_running_loop().time() >= deadline:
                self._emit("reimport_required")
                raise EnjiHttpError(
                    "AUTH_IMPORT_REQUIRED",
                    "refresh outcome is terminal; import a fresh browser credential",
                )
            await asyncio.sleep(TERMINAL_POLL_SECONDS)

    async def recover_startup(self) -> StoredAuth | None:
        """Mark abandoned dispatched work unknown without issuing a request."""

        return await asyncio.to_thread(self._recover_startup)

    def _prepare(self, expected: StoredAuth | None) -> _Preparation:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            loaded = load_auth(self._auth_path)
            current = _loaded_or_raise(loaded)
            if expected is not None and current["revision"] != expected["revision"]:
                return _ReturnAuth(current)
            credential = current["credential"]
            if credential["type"] != "cookie":
                raise EnjiHttpError("AUTH_REQUIRED", "stored credential is not cookie based")

            journal_preparation = self._recover_or_wait(current)
            if journal_preparation is not None:
                return journal_preparation

            reserved = transition(Ready(current["revision"]), Begin(current["revision"])).state
            assert isinstance(reserved, Reserved)
            write_journal(self._auth_path, reserved, failpoint=self._storage_failpoint)
            requested_transition = transition(reserved, DispatchBegun())
            assert isinstance(requested_transition.state, Requested)
            write_journal(self._auth_path, requested_transition.state, failpoint=self._storage_failpoint)
            return _Dispatch(current)

    def _recover_or_wait(self, current: StoredAuth) -> _ReturnAuth | _WaitForRevision | None:
        journal = load_journal(self._auth_path)
        if isinstance(journal, (JournalCorrupt, JournalIoFailure)):
            raise EnjiHttpError("STORAGE", _journal_error_message(journal))
        if not isinstance(journal, JournalLoaded):
            return None
        state = journal.state
        if isinstance(state, Ready):
            raise EnjiHttpError("STORAGE", "refresh journal contains an invalid ready state")
        if state.source_revision != current["revision"]:
            delete_journal(self._auth_path, failpoint=self._storage_failpoint)
            return None
        if isinstance(state, Rotated):
            return self._recover_rotated(state)
        if isinstance(state, Reserved):
            delete_journal(self._auth_path, failpoint=self._storage_failpoint)
            return None
        if isinstance(state, (Requested, Rejected, OutcomeUnknown)):
            return _WaitForRevision(state.source_revision)
        return None

    def _recover_rotated(self, state: Rotated) -> _ReturnAuth:
        recovered = cas_replace_cookie(
            self._auth_path,
            state.source_revision,
            state.replacement_cookie_header,
            failpoint=self._storage_failpoint,
        )
        if isinstance(recovered, CasWritten):
            delete_journal(self._auth_path, failpoint=self._storage_failpoint)
            self._emit("recovered")
            return _ReturnAuth(recovered.auth)
        assert isinstance(recovered, CasSuperseded)
        self._emit("superseded")
        return _ReturnAuth(_loaded_or_raise(load_auth(self._auth_path)))

    def _commit_response(self, source: StoredAuth, response: EnjiHttpResponse) -> StoredAuth:
        cookie_header = _successful_replacement(source, response)
        if cookie_header is not None:
            return self._commit_success(source["revision"], cookie_header)
        if _is_confirmed_refresh_rejection(response):
            return self._commit_rejected(source["revision"], f"refresh rejected with HTTP {response.status_code}")
        return self._commit_unknown(source["revision"], f"ambiguous refresh response HTTP {response.status_code}")

    def _commit_success(self, source_revision: str, cookie_header: str) -> StoredAuth:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            if _is_superseded(self._auth_path, source_revision):
                self._emit("superseded")
                return _loaded_or_raise(load_auth(self._auth_path))
            state = Requested(source_revision)
            rotated_transition = transition(state, ExchangeSucceeded(cookie_header))
            assert isinstance(rotated_transition.state, Rotated)
            write_journal(self._auth_path, rotated_transition.state, failpoint=self._storage_failpoint)
            result = cas_replace_cookie(
                self._auth_path, source_revision, cookie_header, failpoint=self._storage_failpoint
            )
            if isinstance(result, CasWritten):
                delete_journal(self._auth_path, failpoint=self._storage_failpoint)
                self._emit("rotated")
                return result.auth
            self._emit("superseded")
            return _loaded_or_raise(load_auth(self._auth_path))

    def _commit_rejected(self, source_revision: str, reason: str) -> StoredAuth:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            if not _is_superseded(self._auth_path, source_revision):
                state = transition(Requested(source_revision), ExchangeRejected(reason)).state
                assert isinstance(state, Rejected)
                write_journal(self._auth_path, state, failpoint=self._storage_failpoint)
                self._emit("rejected")
            else:
                self._emit("superseded")
                return _loaded_or_raise(load_auth(self._auth_path))
        raise EnjiHttpError("AUTH_REQUIRED", "stored refresh cookie is not authenticated")

    def _commit_unknown(self, source_revision: str, reason: str) -> StoredAuth:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            if not _is_superseded(self._auth_path, source_revision):
                state = transition(Requested(source_revision), ExchangeOutcomeUnknown(reason)).state
                assert isinstance(state, OutcomeUnknown)
                write_journal(self._auth_path, state, failpoint=self._storage_failpoint)
                self._emit("outcome_unknown")
            else:
                self._emit("superseded")
                return _loaded_or_raise(load_auth(self._auth_path))
        raise EnjiHttpError("AUTH_IMPORT_REQUIRED", "refresh outcome is unknown; import a fresh browser credential")

    def _recover_startup(self) -> StoredAuth | None:
        with auth_file_lock(self._auth_path, failpoint=self._storage_failpoint):
            loaded = load_auth(self._auth_path)
            if not isinstance(loaded, AuthLoaded):
                return None
            journal = load_journal(self._auth_path)
            if isinstance(journal, JournalLoaded):
                state = journal.state
                if isinstance(state, Ready):
                    raise EnjiHttpError("STORAGE", "refresh journal contains an invalid ready state")
                if state.source_revision != loaded.auth["revision"]:
                    delete_journal(self._auth_path, failpoint=self._storage_failpoint)
                elif isinstance(state, Rotated):
                    result = cas_replace_cookie(
                        self._auth_path,
                        state.source_revision,
                        state.replacement_cookie_header,
                        failpoint=self._storage_failpoint,
                    )
                    if isinstance(result, CasWritten):
                        delete_journal(self._auth_path, failpoint=self._storage_failpoint)
                        self._emit("recovered")
                        return result.auth
                elif isinstance(state, Reserved):
                    delete_journal(self._auth_path, failpoint=self._storage_failpoint)
                elif isinstance(state, Requested):
                    unknown = transition(state, ExchangeOutcomeUnknown("process exited after refresh dispatch")).state
                    assert isinstance(unknown, OutcomeUnknown)
                    write_journal(self._auth_path, unknown, failpoint=self._storage_failpoint)
                    self._emit("outcome_unknown")
            return loaded.auth

    def _emit(self, outcome: str, **fields: object) -> None:
        """Report only stable classifications; never credentials, paths, or messages."""
        if self._event_sink is not None:
            self._event_sink(_LOGGER, logging.INFO, f"enji_auth_rotation_{outcome}", fields)


def import_credential(auth_path: Path, auth: StoredAuth) -> StoredAuth:
    """Linearize explicit import before clearing any older source journal."""

    with auth_file_lock(auth_path):
        write_auth_file(auth_path, auth)
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
            raise EnjiHttpError("AUTH_REQUIRED", f"auth file is corrupt: {detail}")
        case AuthUnsupported(version=version):
            raise EnjiHttpError("AUTH_REQUIRED", f"auth file version is unsupported: {version!r}")
        case AuthIoFailure(operation=operation, error=error):
            raise EnjiHttpError("STORAGE", f"{operation} failed: {error}")
        case _:
            raise TypeError(f"unexpected auth load result: {type(loaded).__name__}")


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
    except (CookieError, ValueError):
        # Once the refresh POST has returned, malformed cookie protocol data is
        # ambiguous: the server may already have consumed the one-time cookie.
        return None


def _is_confirmed_refresh_rejection(response: EnjiHttpResponse) -> bool:
    if response.status_code not in {401, 403}:
        return False
    try:
        payload = response.json(operation="auth refresh rejection")
    except EnjiHttpError:
        return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else payload.get("code")
    return code in {"AUTH_REQUIRED", "AUTH_INVALID", "UNAUTHENTICATED"}
