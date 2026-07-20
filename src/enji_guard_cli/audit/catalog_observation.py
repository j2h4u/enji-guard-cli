"""Audit-owned catalog observation and persistence.

The upstream catalog is observed after a live gateway fetch.  Persistence is
kept behind this small typed service so delivery and HTTP adapters do not own
catalog-change state or request-scoped context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from enji_guard_cli.atomic_json import write_atomic_json
from enji_guard_cli.audit.ports import AuditCatalogAction, AuditCatalogChange, AuditCatalogResult

_SCHEMA_VERSION = 2
type _ChangeKind = Literal["added", "removed", "changed"]


@dataclass(frozen=True, slots=True)
class AuditCatalogObservation:
    """The typed result of one catalog observation."""

    result: AuditCatalogResult

    @property
    def changes(self) -> tuple[AuditCatalogChange, ...]:
        return self.result.changes


class AuditCatalogSnapshotRepository(Protocol):
    """Persistence port for the Audit catalog baseline."""

    def load(self) -> tuple[AuditCatalogAction, ...] | None: ...

    def save(self, actions: tuple[AuditCatalogAction, ...]) -> None: ...


class AuditCatalogObserver:
    """Compare live catalog actions and persist a safe account snapshot."""

    def __init__(self, state_file: Path | AuditCatalogSnapshotRepository) -> None:
        self.repository = FileAuditCatalogSnapshotRepository(state_file) if isinstance(state_file, Path) else state_file
        self._changes: tuple[AuditCatalogChange, ...] = ()

    @property
    def changes(self) -> tuple[AuditCatalogChange, ...]:
        return self._changes

    def observe(self, result: AuditCatalogResult) -> AuditCatalogResult:
        previous = self.repository.load()
        current = tuple(action for action in result.actions if _is_published_audit(action))
        changes = _diff(previous, current) if previous is not None else ()
        if previous is None or changes:
            self.repository.save(current)
        self._changes = changes
        return AuditCatalogResult(result.actions, result.autofixes, changes)


def _is_published_audit(action: AuditCatalogAction) -> bool:
    return action.action_key != "audit.recon" and action.category == "audit" and action.status == "published"


class FileAuditCatalogSnapshotRepository:
    """Atomic JSON implementation of the typed catalog snapshot port."""

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file

    def load(self) -> tuple[AuditCatalogAction, ...] | None:
        payload = _read_snapshot(self.state_file)
        if payload is None:
            return None
        return tuple(_action_from_snapshot(key, value) for key, value in sorted(payload.items()))

    def save(self, actions: tuple[AuditCatalogAction, ...]) -> None:
        _write_snapshot(self.state_file, {action.action_key: _action_payload(action) for action in actions})


def _read_snapshot(state_file: Path) -> dict[str, dict[str, object]] | None:
    try:
        payload = cast(object, json.loads(state_file.read_text(encoding="utf-8")))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
        return None
    actions = payload.get("audits")
    if not isinstance(actions, dict):
        return None
    return {
        key: cast(dict[str, object], value)
        for key, value in actions.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def _write_snapshot(state_file: Path, actions: dict[str, dict[str, object]]) -> None:
    write_atomic_json(state_file, {"schema_version": _SCHEMA_VERSION, "audits": actions})


def _diff(
    previous: tuple[AuditCatalogAction, ...], current: tuple[AuditCatalogAction, ...]
) -> tuple[AuditCatalogChange, ...]:
    changes: list[AuditCatalogChange] = []
    order: dict[_ChangeKind, int] = {"added": 0, "removed": 1, "changed": 2}
    previous_by_key = {item.action_key: item for item in previous}
    current_by_key = {item.action_key: item for item in current}
    for action_key in sorted(previous_by_key.keys() | current_by_key.keys()):
        old = previous_by_key.get(action_key)
        new = current_by_key.get(action_key)
        if old is None and new is not None:
            changes.append(AuditCatalogChange("added", action_key))
        elif old is not None and new is None:
            changes.append(AuditCatalogChange("removed", action_key))
        elif old is not None and new is not None and old != new:
            fields = tuple(name for name, before, after in _action_fields(old, new) if before != after)
            changes.append(AuditCatalogChange("changed", action_key, fields))
    return tuple(sorted(changes, key=lambda change: (order[change.kind], change.action_key)))


def _action_fields(old: AuditCatalogAction, new: AuditCatalogAction) -> tuple[tuple[str, object, object], ...]:
    return (
        ("title", old.title, new.title),
        ("category", old.category, new.category),
        ("status", old.status, new.status),
        ("metric_group", old.metric_group, new.metric_group),
        ("runbook_kind", old.runbook_kind, new.runbook_kind),
        ("runbook_id", old.runbook_id, new.runbook_id),
        ("artifact_schema_name", old.artifact_schema_name, new.artifact_schema_name),
        ("artifact_schema_version", old.artifact_schema_version, new.artifact_schema_version),
        ("task_description_template", old.task_description_template, new.task_description_template),
    )


def _action_payload(action: AuditCatalogAction) -> dict[str, object]:
    return {
        "title": action.title,
        "category": action.category,
        "status": action.status,
        "metric_group": action.metric_group,
        "runbook_kind": action.runbook_kind,
        "runbook_id": action.runbook_id,
        "artifact_schema_name": action.artifact_schema_name,
        "artifact_schema_version": action.artifact_schema_version,
        "task_description_template": action.task_description_template,
    }


def _action_from_snapshot(action_key: str, payload: dict[str, object]) -> AuditCatalogAction:
    def string_value(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else None

    return AuditCatalogAction(
        action_key=action_key,
        title=string_value("title") or "",
        category=string_value("category"),
        status=string_value("status"),
        metric_group=string_value("metric_group"),
        runbook_kind=string_value("runbook_kind"),
        runbook_id=string_value("runbook_id"),
        artifact_schema_name=string_value("artifact_schema_name"),
        artifact_schema_version=string_value("artifact_schema_version"),
        task_description_template=string_value("task_description_template"),
    )


__all__ = [
    "AuditCatalogObservation",
    "AuditCatalogObserver",
    "AuditCatalogSnapshotRepository",
    "FileAuditCatalogSnapshotRepository",
]
