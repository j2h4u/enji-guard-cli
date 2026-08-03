"""Closed domain model for one cookie-refresh credential generation.

This module is the executable contract for refresh safety.  Its invariants are:

* :func:`transition` is pure and total: it performs no I/O, reads no clock, and
  returns declarative effects for the coordinator to execute.
* A credential revision identifies one refresh-token generation.  Importing
  credentials always creates a new revision, even when the bytes are identical.
* ``Reserved`` means no request was dispatched and is therefore recoverable.
  ``Requested`` means dispatch began and must never lead to another automatic
  dispatch for that revision.
* Only a protocol-confirmed invalid refresh token becomes ``Rejected``.  Any
  transport failure, cancellation, proxy-shaped response, malformed response,
  or post-dispatch uncertainty becomes ``OutcomeUnknown``.
* ``Rotated`` retains the complete successor until it is durably projected.
  The source revision may be replaced only with compare-and-swap semantics.
* ``Rejected`` and ``OutcomeUnknown`` do not mean that the access credential is
  already unusable.  They stop rotation of that revision while observers may
  continue using it until ordinary authentication fails.
* Recovery and observation never hide a terminal outcome.  Durable outbox
  delivery is separate from rotation progress and may be repeated by event key.

The journal parser deliberately lives in :mod:`store`.  This module accepts
only already-validated domain values, so malformed files can never become a
state-machine input by accident.  Backend-specific adjudication of an unknown
outcome belongs to the refresh loop, not to this pure model.
"""

from dataclasses import dataclass, field
from typing import Literal, Never, assert_never
from uuid import uuid4


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
    successor_revision: str = field(default_factory=lambda: uuid4().hex)


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
    successor_revision: str = field(default_factory=lambda: uuid4().hex)


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


@dataclass(frozen=True, slots=True)
class SourceRevisionAlive:
    """A read-only probe proved the source credential still authenticates.

    This is the only way out of ``OutcomeUnknown`` that does not require an
    import, and it is not a replay: the verdict comes from a request that
    carried the credential we already hold, never the one-time refresh token.
    A live source revision means the exchange never rotated it.
    """


RotationEvent = (
    Begin
    | DispatchBegun
    | ExchangeSucceeded
    | ExchangeRejected
    | ExchangeOutcomeUnknown
    | Imported
    | Recover
    | SourceRevisionAlive
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
    successor_revision: str


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

    Effects describe required work but do not imply it has happened.  The
    coordinator must persist the returned state before executing a dispatch
    effect.  ``Requested`` never transitions back to a dispatching state: an
    uncertain POST result is parked until import supersedes the revision or the
    refresh loop explicitly adjudicates the still-live source credential.
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
        case ExchangeSucceeded(replacement_cookie_header=replacement, successor_revision=successor_revision):
            rotated = Rotated(state.source_revision, replacement, successor_revision)
            return Transition(
                rotated,
                (PersistJournal(rotated), PersistReplacement(state.source_revision, replacement, successor_revision)),
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
            return Transition(
                state,
                (PersistReplacement(state.source_revision, state.replacement_cookie_header, state.successor_revision),),
            )
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
        case SourceRevisionAlive() if isinstance(state, OutcomeUnknown):
            # Only ambiguity is adjudicable.  `Rejected` is a confirmed answer
            # from the server, so a live probe there would be a contradiction
            # to investigate, not a state to clear.
            return Transition(Ready(state.source_revision), (DeleteJournal(),))
        case _:
            return _invalid_transition(state, event)


def _invalid_transition(state: RotationState, event: RotationEvent) -> Never:
    # The pattern matches above provide exhaustive handling for the closed
    # unions.  Reaching here is a programmer error, not an external-input path.
    raise InvalidTransitionError(f"{type(event).__name__} is invalid for {type(state).__name__}")


RotationOutcome = Literal["rotated", "rejected", "outcome_unknown", "adjudicated_alive"]


@dataclass(frozen=True, slots=True)
class RotationEventMetadata:
    """Non-secret durable outbox identity for a terminal rotation outcome."""

    outcome: RotationOutcome
    event_key: str


def rotation_event_metadata(state: Rotated | Rejected | OutcomeUnknown) -> RotationEventMetadata:
    """Return the deterministic event identity stored with a terminal journal."""

    match state:
        case Rotated(source_revision=source_revision):
            outcome: RotationOutcome = "rotated"
        case Rejected(source_revision=source_revision):
            outcome = "rejected"
        case OutcomeUnknown(source_revision=source_revision):
            outcome = "outcome_unknown"
        case _ as impossible:
            assert_never(impossible)
    return RotationEventMetadata(outcome, rotation_event_key(source_revision, outcome))


def rotation_event_key(source_revision: str, outcome: RotationOutcome) -> str:
    """Build a stable outbox key without credential or diagnostic material."""

    return f"auth-rotation:{source_revision}:{outcome}"
