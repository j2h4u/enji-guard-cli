"""Pure, closed observer projection over typed auth storage results.

No projection performs I/O, refreshes credentials, or exposes journal parser
details to status/readiness callers.  Runtime integration deliberately lands
in a later slice.
"""

from dataclasses import dataclass
from typing import assert_never

from enji_guard_cli.auth_session.state_machine import (
    OutcomeUnknown,
    Ready,
    Rejected,
    Requested,
    Reserved,
    Rotated,
    RotationState,
)
from enji_guard_cli.auth_session.store import (
    AuthAbsent,
    AuthClockAnomaly,
    AuthCorrupt,
    AuthIoFailure,
    AuthLoaded,
    AuthLoadResult,
    AuthUnsupported,
    JournalAbsent,
    JournalCorrupt,
    JournalIoFailure,
    JournalLoaded,
    JournalLoadResult,
    StoredAuth,
)


@dataclass(frozen=True, slots=True)
class CredentialReady:
    auth: StoredAuth


@dataclass(frozen=True, slots=True)
class RotationReserved:
    auth: StoredAuth
    state: Reserved


@dataclass(frozen=True, slots=True)
class RotationInProgress:
    auth: StoredAuth
    state: Requested


@dataclass(frozen=True, slots=True)
class RotationRecoveryAvailable:
    auth: StoredAuth
    state: Rotated


@dataclass(frozen=True, slots=True)
class ReimportRequired:
    auth: StoredAuth
    state: Rejected | OutcomeUnknown


@dataclass(frozen=True, slots=True)
class CredentialAbsent:
    pass


@dataclass(frozen=True, slots=True)
class CredentialCorrupt:
    detail: str


@dataclass(frozen=True, slots=True)
class CredentialUnsupported:
    version: object


@dataclass(frozen=True, slots=True)
class CredentialIoFailure:
    operation: str
    error: OSError


@dataclass(frozen=True, slots=True)
class CredentialClockAnomaly:
    field: str


@dataclass(frozen=True, slots=True)
class JournalCorruptProjection:
    detail: str


@dataclass(frozen=True, slots=True)
class JournalIoFailureProjection:
    operation: str
    error: OSError


@dataclass(frozen=True, slots=True)
class JournalImpossibleState:
    state: Ready | Reserved | Requested | Rotated | Rejected | OutcomeUnknown


AuthProjection = (
    CredentialReady
    | RotationReserved
    | RotationInProgress
    | RotationRecoveryAvailable
    | ReimportRequired
    | CredentialAbsent
    | CredentialCorrupt
    | CredentialUnsupported
    | CredentialIoFailure
    | CredentialClockAnomaly
    | JournalCorruptProjection
    | JournalIoFailureProjection
    | JournalImpossibleState
)


def project_auth(auth_result: AuthLoadResult, journal_result: JournalLoadResult) -> AuthProjection:
    """Classify a typed storage snapshot without collapsing observer states."""

    match auth_result:
        case AuthLoaded(auth=auth):
            return _project_loaded(auth, journal_result)
        case AuthAbsent():
            return CredentialAbsent()
        case AuthCorrupt(detail=detail):
            return CredentialCorrupt(detail)
        case AuthUnsupported(version=version):
            return CredentialUnsupported(version)
        case AuthIoFailure(operation=operation, error=error):
            return CredentialIoFailure(operation, error)
        case AuthClockAnomaly(field=field):
            return CredentialClockAnomaly(field)
        case _ as impossible:
            assert_never(impossible)


def _project_loaded(auth: StoredAuth, journal_result: JournalLoadResult) -> AuthProjection:
    match journal_result:
        case JournalAbsent():
            return CredentialReady(auth)
        case JournalCorrupt(detail=detail):
            return JournalCorruptProjection(detail)
        case JournalIoFailure(operation=operation, error=error):
            return JournalIoFailureProjection(operation, error)
        case JournalLoaded(state=state):
            return _project_rotation_state(auth, state)
        case _ as impossible:
            assert_never(impossible)


def _project_rotation_state(auth: StoredAuth, state: RotationState) -> AuthProjection:
    if isinstance(state, Ready):
        return JournalImpossibleState(state)
    if state.source_revision != auth["revision"]:
        return CredentialReady(auth)
    match state:
        case Reserved():
            return RotationReserved(auth, state)
        case Requested():
            return RotationInProgress(auth, state)
        case Rotated():
            return RotationRecoveryAvailable(auth, state)
        case Rejected() | OutcomeUnknown():
            return ReimportRequired(auth, state)
        case _ as impossible:
            assert_never(impossible)
