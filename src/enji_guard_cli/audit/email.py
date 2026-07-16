"""Audit completion-email policy and batch workflow."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from enji_guard_cli.audit.ports import AuditEmailPreference, AuditEmailPreferenceUpdate


@dataclass(frozen=True, slots=True)
class EmailPreferencesUpdate:
    manual: bool | None = None
    scheduled: bool | None = None


def validate_update(update: EmailPreferencesUpdate) -> AuditEmailPreferenceUpdate:
    if update.manual is None and update.scheduled is None:
        raise ValueError("pass --manual or --scheduled")
    return AuditEmailPreferenceUpdate(update.manual, update.scheduled)


class EmailPreferenceGateway(Protocol):
    def list_email_preferences(self, repo_id: str, audit_keys: tuple[str, ...]) -> tuple[AuditEmailPreference, ...]: ...

    def set_email_preference(
        self, repo_id: str, audit_key: str, update: AuditEmailPreferenceUpdate
    ) -> AuditEmailPreference: ...


def list_for_targets(
    targets: Sequence[object], audit_keys: tuple[str, ...], gateway: EmailPreferenceGateway
) -> tuple[tuple[object, tuple[AuditEmailPreference, ...]], ...]:
    return tuple((target, gateway.list_email_preferences(_repo_id(target), audit_keys)) for target in targets)


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
