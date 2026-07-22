"""Closed domain model for a single cookie-refresh revision.

The journal parser deliberately lives in :mod:`store`.  This module accepts
only already-validated domain values, so malformed files can never become a
state-machine input by accident.
"""

from dataclasses import dataclass
from typing import Never, assert_never


class InvalidTransitionError(ValueError):
    """Raised when an internal caller attempts an impossible transition."""


@dataclass(frozen=True, slots=True)
class Ready:
    revision: str


@dataclass(frozen=True, slots=True)
class Reserved:
    source_revision: str


@dataclass(frozen=True, slots=True)
class Requested:
    source_revision: str


@dataclass(frozen=True, slots=True)
class Rotated:
    source_revision: str
    replacement_cookie_header: str


@dataclass(frozen=True, slots=True)
class Rejected:
    source_revision: str
    reason: str


@dataclass(frozen=True, slots=True)
class OutcomeUnknown:
    source_revision: str
    reason: str


RotationState = Ready | Reserved | Requested | Rotated | Rejected | OutcomeUnknown


@dataclass(frozen=True, slots=True)
class Begin:
    source_revision: str


@dataclass(frozen=True, slots=True)
class DispatchBegun:
    pass


@dataclass(frozen=True, slots=True)
class ExchangeSucceeded:
    replacement_cookie_header: str


@dataclass(frozen=True, slots=True)
class ExchangeRejected:
    reason: str


@dataclass(frozen=True, slots=True)
class ExchangeOutcomeUnknown:
    reason: str


@dataclass(frozen=True, slots=True)
class Imported:
    revision: str


@dataclass(frozen=True, slots=True)
class Recover:
    pass


RotationEvent = (
    Begin | DispatchBegun | ExchangeSucceeded | ExchangeRejected | ExchangeOutcomeUnknown | Imported | Recover
)


@dataclass(frozen=True, slots=True)
class PersistJournal:
    state: RotationState


@dataclass(frozen=True, slots=True)
class DispatchExchange:
    source_revision: str


@dataclass(frozen=True, slots=True)
class PersistReplacement:
    source_revision: str
    replacement_cookie_header: str


@dataclass(frozen=True, slots=True)
class DeleteJournal:
    pass


@dataclass(frozen=True, slots=True)
class WaitForTerminalRevision:
    source_revision: str


Effect = PersistJournal | DispatchExchange | PersistReplacement | DeleteJournal | WaitForTerminalRevision


@dataclass(frozen=True, slots=True)
class Transition:
    state: RotationState
    effects: tuple[Effect, ...]


def transition(state: RotationState, event: RotationEvent) -> Transition:
    """Apply one pure, total domain transition.

    ``Requested`` never transitions back to a dispatching state.  That is the
    one-time-token invariant: an uncertain POST result is terminal until an
    explicit credential import creates a new revision.
    """

    match state:
        case Ready():
            return _ready_transition(state, event)
        case Reserved():
            return _reserved_transition(state, event)
        case Requested():
            return _requested_transition(state, event)
        case Rotated():
            return _rotated_transition(state, event)
        case Rejected() | OutcomeUnknown():
            return _terminal_transition(state, event)
        case _ as impossible:
            assert_never(impossible)


def _ready_transition(state: Ready, event: RotationEvent) -> Transition:
    match event:
        case Begin(source_revision=source_revision):
            reserved = Reserved(source_revision)
            return Transition(reserved, (PersistJournal(reserved),))
        case Imported(revision=revision):
            return Transition(Ready(revision), ())
        case _:
            return _invalid_transition(state, event)


def _reserved_transition(state: Reserved, event: RotationEvent) -> Transition:
    match event:
        case DispatchBegun():
            requested = Requested(state.source_revision)
            return Transition(requested, (PersistJournal(requested), DispatchExchange(state.source_revision)))
        case Recover():
            return Transition(Ready(state.source_revision), (DeleteJournal(),))
        case Imported(revision=revision):
            return Transition(Ready(revision), (DeleteJournal(),))
        case _:
            return _invalid_transition(state, event)


def _requested_transition(state: Requested, event: RotationEvent) -> Transition:
    match event:
        case ExchangeSucceeded(replacement_cookie_header=replacement):
            rotated = Rotated(state.source_revision, replacement)
            return Transition(
                rotated, (PersistJournal(rotated), PersistReplacement(state.source_revision, replacement))
            )
        case ExchangeRejected(reason=reason):
            rejected = Rejected(state.source_revision, reason)
            return Transition(rejected, (PersistJournal(rejected),))
        case ExchangeOutcomeUnknown(reason=reason):
            unknown = OutcomeUnknown(state.source_revision, reason)
            return Transition(unknown, (PersistJournal(unknown),))
        case Imported(revision=revision):
            return Transition(Ready(revision), (DeleteJournal(),))
        case Recover():
            unknown = OutcomeUnknown(state.source_revision, "interrupted after refresh dispatch")
            return Transition(unknown, (PersistJournal(unknown),))
        case _:
            return _invalid_transition(state, event)


def _rotated_transition(state: Rotated, event: RotationEvent) -> Transition:
    match event:
        case Recover():
            return Transition(state, (PersistReplacement(state.source_revision, state.replacement_cookie_header),))
        case Imported(revision=revision):
            return Transition(Ready(revision), (DeleteJournal(),))
        case _:
            return _invalid_transition(state, event)


def _terminal_transition(state: Rejected | OutcomeUnknown, event: RotationEvent) -> Transition:
    match event:
        case Imported(revision=revision):
            return Transition(Ready(revision), (DeleteJournal(),))
        case Recover():
            return Transition(state, (WaitForTerminalRevision(state.source_revision),))
        case _:
            return _invalid_transition(state, event)


def _invalid_transition(state: RotationState, event: RotationEvent) -> Never:
    # The pattern matches above provide exhaustive handling for the closed
    # unions.  Reaching here is a programmer error, not an external-input path.
    raise InvalidTransitionError(f"{type(event).__name__} is invalid for {type(state).__name__}")
