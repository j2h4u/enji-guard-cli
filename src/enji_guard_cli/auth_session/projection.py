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


@dataclass(frozen=True, slots=True)
class AuthProjectionError(Exception):
    """Stable observer-facing failure derived from a typed projection."""

    code: str
    message: str


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


_STATIC_PROJECTION_ERRORS: dict[type[object], tuple[str, str]] = {
    RotationRecoveryAvailable: (
        "AUTH_ROTATION_RECOVERY_PENDING",
        "auth rotation recovery is pending; restart the service or import a fresh browser credential",
    ),
    CredentialAbsent: ("AUTH_REQUIRED", "auth file does not exist"),
    CredentialCorrupt: ("AUTH_CORRUPT", "auth file is corrupt; import a fresh browser credential"),
    CredentialUnsupported: ("AUTH_UNSUPPORTED", "auth file version is unsupported; import a fresh browser credential"),
    CredentialIoFailure: ("AUTH_IO_FAILURE", "auth credential storage is unavailable"),
    CredentialClockAnomaly: ("AUTH_CLOCK_ANOMALY", "auth file imported_at is in the future"),
    JournalCorruptProjection: ("AUTH_JOURNAL_CORRUPT", "refresh journal is corrupt; import a fresh browser credential"),
    JournalIoFailureProjection: ("AUTH_JOURNAL_IO_FAILURE", "refresh journal storage is unavailable"),
    JournalImpossibleState: ("AUTH_JOURNAL_INVALID", "refresh journal contains an invalid state"),
}


def network_credential(projection: AuthProjection) -> StoredAuth:
    """Return an observationally usable credential without mutating storage."""

    match projection:
        case CredentialReady(auth=auth) | RotationReserved(auth=auth) | RotationInProgress(auth=auth):
            return auth
        case ReimportRequired(state=Rejected()):
            raise AuthProjectionError(
                "AUTH_REFRESH_REJECTED", "refresh was rejected; import a fresh browser credential"
            )
        case ReimportRequired(state=OutcomeUnknown()):
            raise AuthProjectionError(
                "AUTH_REFRESH_OUTCOME_UNKNOWN", "refresh outcome is unknown; import a fresh browser credential"
            )
        case _:
            error = _STATIC_PROJECTION_ERRORS.get(type(projection))
            if error is not None:
                raise AuthProjectionError(*error)
            raise AssertionError(f"unexpected auth projection: {type(projection).__name__}")


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
