"""Surface-neutral result contract for explicit, fail-fast batch mutations."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from enji_guard_cli.application.portfolio_views import RepositoryRefView


class MutationReason(StrEnum):
    """Stable explanation codes for an individual mutation outcome."""

    APPLIED = "APPLIED"
    ALREADY_EFFECTIVE = "ALREADY_EFFECTIVE"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


@dataclass(frozen=True, slots=True)
class MutationTargetView:
    """One repository-scoped setting, using only operator-facing selectors."""

    repository: RepositoryRefView
    selector: str


@dataclass(frozen=True, slots=True)
class MutationDecision:
    """A fully planned mutation that has not yet been dispatched."""

    target: MutationTargetView
    status: Literal["changed", "unchanged"]
    reason: MutationReason
    apply: Callable[[], None]


@dataclass(frozen=True, slots=True)
class MutationOutcome:
    """The observed result for one attempted mutation."""

    target: MutationTargetView
    status: Literal["changed", "unchanged", "failed"]
    reason: MutationReason | None
    code: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class BatchMutationResult:
    """A deterministic sequential batch result; failed work is never rolled back."""

    status: Literal["completed", "partial", "failed"]
    total: int
    completed: int
    remaining: int
    changed: int
    unchanged: int
    failed: int
    results: tuple[MutationOutcome, ...]


class MutationOperationalError(Exception):
    """A translated, operator-facing failure that may safely end a batch."""

    def __init__(self, code: str, message: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.outcome_unknown = outcome_unknown


def execute_batch(decisions: Sequence[MutationDecision]) -> BatchMutationResult:
    """Apply pre-planned work sequentially and stop after the first operational failure.

    Validation and programming errors deliberately escape: callers must validate and
    construct the full plan before this executor owns any write.
    """
    total = len(decisions)
    outcomes: list[MutationOutcome] = []
    changed = 0
    unchanged = 0
    for decision in decisions:
        if decision.status == "unchanged":
            unchanged += 1
            outcomes.append(MutationOutcome(decision.target, "unchanged", decision.reason))
            continue
        try:
            decision.apply()
        except MutationOperationalError as exc:
            outcomes.append(
                MutationOutcome(
                    decision.target,
                    "failed",
                    MutationReason.OUTCOME_UNKNOWN if exc.outcome_unknown else None,
                    exc.code,
                    exc.message,
                )
            )
            completed = changed + unchanged
            return BatchMutationResult(
                "partial" if completed else "failed",
                total,
                completed,
                total - completed,
                changed,
                unchanged,
                1,
                tuple(outcomes),
            )
        changed += 1
        outcomes.append(MutationOutcome(decision.target, "changed", decision.reason))
    return BatchMutationResult("completed", total, total, 0, changed, unchanged, 0, tuple(outcomes))


__all__ = [
    "BatchMutationResult",
    "MutationDecision",
    "MutationOperationalError",
    "MutationOutcome",
    "MutationReason",
    "MutationTargetView",
    "execute_batch",
]
