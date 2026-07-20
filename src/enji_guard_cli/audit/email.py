"""Audit completion-email policy and batch workflow."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from enji_guard_cli.audit.ports import AuditEmailPreference, AuditEmailPreferenceUpdate
from enji_guard_cli.fanout import BoundedFanout


@dataclass(frozen=True, slots=True)
class EmailPreferencesUpdate:
    manual: bool | None = None
    scheduled: bool | None = None


def validate_update(update: EmailPreferencesUpdate) -> AuditEmailPreferenceUpdate:
    if update.manual is None and update.scheduled is None:
        raise ValueError("pass --manual or --scheduled")
    return AuditEmailPreferenceUpdate(update.manual, update.scheduled)


class EmailPreferenceGateway(Protocol):
    def get_email_preferences(self, repo_id: str, audit_key: str) -> AuditEmailPreference: ...

    def set_email_preference(
        self, repo_id: str, audit_key: str, update: AuditEmailPreferenceUpdate
    ) -> AuditEmailPreference: ...


def list_for_targets(
    targets: Sequence[object],
    audit_keys: tuple[str, ...],
    gateway: EmailPreferenceGateway,
    fanout: BoundedFanout,
) -> tuple[tuple[object, tuple[AuditEmailPreference, ...]], ...]:
    resolved_targets = tuple(targets)
    jobs = tuple((_repo_id(target), key) for target in resolved_targets for key in audit_keys)
    if not jobs:
        return tuple((target, ()) for target in resolved_targets)
    preferences = fanout.map(jobs, lambda job: gateway.get_email_preferences(*job))
    width = len(audit_keys)
    return tuple(
        (target, preferences[index * width : (index + 1) * width]) for index, target in enumerate(resolved_targets)
    )


def set_for_targets(
    targets: Sequence[object],
    audit_keys: tuple[str, ...],
    update: EmailPreferencesUpdate,
    gateway: EmailPreferenceGateway,
) -> tuple[AuditEmailPreference, ...]:
    typed = validate_update(update)
    return tuple(gateway.set_email_preference(_repo_id(target), key, typed) for target in targets for key in audit_keys)


def _repo_id(target: object) -> str:
    value = getattr(target, "repo_id", None)
    if not isinstance(value, str):
        raise TypeError("email preference target must expose repo_id")
    return value


__all__ = ["EmailPreferencesUpdate", "list_for_targets", "set_for_targets", "validate_update"]
