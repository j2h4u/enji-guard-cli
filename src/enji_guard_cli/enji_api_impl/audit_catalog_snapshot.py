import json
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, cast

from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

AUDIT_CATALOG_SCHEMA_VERSION = 1
type AuditCatalogChangeKind = Literal["added", "removed", "changed"]
type AuditCatalogChangeNotifier = Callable[[tuple["AuditCatalogChange", ...]], None]


@dataclass(frozen=True, slots=True)
class AuditCatalogChange:
    kind: AuditCatalogChangeKind
    action_key: str
    previous: JsonObjectPayload | None
    current: JsonObjectPayload | None

    @property
    def selector(self) -> str:
        return self.action_key.removeprefix("audit.")

    @property
    def changed_fields(self) -> tuple[str, ...]:
        if self.previous is None or self.current is None:
            return ()
        return tuple(
            sorted(
                key
                for key in self.previous.keys() | self.current.keys()
                if self.previous.get(key) != self.current.get(key)
            )
        )


@dataclass(slots=True)
class AuditCatalogObservation:
    state_file: Path
    notifier: AuditCatalogChangeNotifier | None
    changes: tuple[AuditCatalogChange, ...] = ()


type AuditCatalogObservationToken = Token[AuditCatalogObservation | None]


_ACTIVE_AUDIT_CATALOG_OBSERVATION: ContextVar[AuditCatalogObservation | None] = ContextVar(
    "enji_audit_catalog_observation", default=None
)


def begin_audit_catalog_observation(
    *,
    state_file: Path,
    notifier: AuditCatalogChangeNotifier | None = None,
) -> AuditCatalogObservationToken:
    return _ACTIVE_AUDIT_CATALOG_OBSERVATION.set(AuditCatalogObservation(state_file=state_file, notifier=notifier))


def end_audit_catalog_observation(token: AuditCatalogObservationToken) -> None:
    _ACTIVE_AUDIT_CATALOG_OBSERVATION.reset(token)


def observe_active_audit_catalog(payload: JsonObjectPayload) -> tuple[AuditCatalogChange, ...]:
    observation = _ACTIVE_AUDIT_CATALOG_OBSERVATION.get()
    if observation is None:
        return ()
    changes = observe_audit_catalog(payload, observation.state_file)
    if changes:
        observation.changes = (*observation.changes, *changes)
    if changes and observation.notifier is not None:
        observation.notifier(changes)
    return changes


def active_audit_catalog_changes() -> tuple[AuditCatalogChange, ...]:
    observation = _ACTIVE_AUDIT_CATALOG_OBSERVATION.get()
    return () if observation is None else observation.changes


def observe_audit_catalog(payload: JsonObjectPayload, state_file: Path) -> tuple[AuditCatalogChange, ...]:
    current = _published_audit_actions(payload)
    if current is None:
        return ()
    previous = _read_snapshot(state_file)
    if previous is None:
        _write_snapshot(state_file, current)
        return ()
    changes = _diff_audit_actions(previous, current)
    if changes:
        _write_snapshot(state_file, current)
    return changes


def _published_audit_actions(payload: JsonObjectPayload) -> dict[str, JsonObjectPayload] | None:
    raw_actions = payload.get("curatedActions")
    if not isinstance(raw_actions, list):
        return None
    actions = [action for action in raw_actions if isinstance(action, dict)]
    if not any(action.get("actionKey") == "audit.recon" for action in actions):
        return None
    audits: dict[str, JsonObjectPayload] = {}
    for action in actions:
        if (
            action.get("actionKey") == "audit.recon"
            or action.get("category") != "audit"
            or action.get("status") != "published"
        ):
            continue
        action_key = action.get("actionKey")
        if not isinstance(action_key, str) or not action_key:
            continue
        audits[action_key] = _json_object(action)
    return dict(sorted(audits.items()))


def _json_object(value: dict[str, JsonValue]) -> JsonObjectPayload:
    return cast(JsonObjectPayload, json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"))))


def _read_snapshot(state_file: Path) -> dict[str, JsonObjectPayload] | None:
    try:
        payload = cast(object, json.loads(state_file.read_text(encoding="utf-8")))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != AUDIT_CATALOG_SCHEMA_VERSION:
        return None
    audits = payload.get("audits")
    if not isinstance(audits, dict):
        return None
    return {
        action_key: cast(JsonObjectPayload, action)
        for action_key, action in audits.items()
        if isinstance(action_key, str) and isinstance(action, dict)
    }


def _write_snapshot(state_file: Path, audits: dict[str, JsonObjectPayload]) -> None:
    try:
        state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=state_file.parent, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(
                {"schema_version": AUDIT_CATALOG_SCHEMA_VERSION, "audits": audits},
                temp_file,
                sort_keys=True,
            )
            temp_file.write("\n")
        temp_path.chmod(0o600)
        temp_path.replace(state_file)
    except OSError:
        return


def _diff_audit_actions(
    previous: dict[str, JsonObjectPayload],
    current: dict[str, JsonObjectPayload],
) -> tuple[AuditCatalogChange, ...]:
    changes: list[AuditCatalogChange] = []
    for action_key in sorted(previous.keys() | current.keys()):
        old = previous.get(action_key)
        new = current.get(action_key)
        if old is None and new is not None:
            changes.append(AuditCatalogChange("added", action_key, None, new))
        elif old is not None and new is None:
            changes.append(AuditCatalogChange("removed", action_key, old, None))
        elif old != new:
            changes.append(AuditCatalogChange("changed", action_key, old, new))
    change_order = {"added": 0, "removed": 1, "changed": 2}
    return tuple(sorted(changes, key=lambda change: (change_order[change.kind], change.action_key)))
