from collections.abc import Mapping

from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import AuditCatalogResult

RECON_ACTION_KEY = "audit.recon"
AUDIT_CATEGORY = "audit"
PUBLISHED_STATUS = "published"


def parse_audit_catalog(payload: Mapping[str, object]) -> AuditCatalog:
    """Interpret the current live catalog as Audit domain definitions."""

    actions = _curated_actions(payload)
    recon = _require_recon(actions)
    published_audits = tuple(_published_audit(action) for action in actions if _is_published_audit_action(action))
    _require_unique_action_keys((recon, *published_audits))
    return AuditCatalog(published_audits=published_audits, recon=recon)


def parse_catalog_result(result: AuditCatalogResult) -> AuditCatalog:
    """Apply the same strict catalog invariants to the typed gateway result."""

    actions = [
        {
            "actionKey": item.action_key,
            "title": item.title,
            "category": item.category,
            "status": item.status,
            "metricGroup": item.metric_group,
            "runbookKind": item.runbook_kind,
            "fleetRunbookId": item.runbook_id,
            "artifactSchemaName": item.artifact_schema_name,
            "artifactSchemaVersion": item.artifact_schema_version,
            "taskDescriptionTemplate": item.task_description_template,
        }
        for item in result.actions
    ]
    return parse_audit_catalog({"curatedActions": actions})


def published_audit_action_keys(catalog: Mapping[str, object]) -> set[str]:
    """Return published audit action keys used by catalog-driven autofixes."""

    return {
        action_key
        for action in _json_object_list(catalog.get("curatedActions"))
        if action.get("status") == PUBLISHED_STATUS
        if action.get("category") == AUDIT_CATEGORY
        if isinstance(action_key := action.get("actionKey"), str)
    }


def published_autofix_keys(catalog: Mapping[str, object]) -> list[tuple[str, str]]:
    """Return published, unique autofix action/variant keys in catalog order."""

    keys: list[tuple[str, str]] = []
    for autofix in _json_object_list(catalog.get("auditAutofixes")):
        action_key = autofix.get("actionKey")
        variant_key = autofix.get("variantKey")
        key = (action_key, variant_key) if isinstance(action_key, str) and isinstance(variant_key, str) else None
        if autofix.get("status") == PUBLISHED_STATUS and key is not None and key not in keys:
            keys.append(key)
    return keys


def _require_recon(actions: list[dict[str, object]]) -> AuditDefinition:
    recon_actions = [action for action in actions if action.get("actionKey") == RECON_ACTION_KEY]
    if len(recon_actions) != 1:
        raise ValueError(f"catalog must contain exactly one {RECON_ACTION_KEY} action")
    return _audit_definition(recon_actions[0], metric_group=None)


def _is_published_audit_action(action: dict[str, object]) -> bool:
    return (
        action.get("actionKey") != RECON_ACTION_KEY
        and action.get("category") == AUDIT_CATEGORY
        and action.get("status") == PUBLISHED_STATUS
    )


def _published_audit(action: dict[str, object]) -> AuditDefinition:
    metric_group = _required_nonempty_str(action, "metricGroup", "published audit action is missing metricGroup")
    return _audit_definition(action, metric_group=metric_group)


def _audit_definition(action: dict[str, object], *, metric_group: str | None) -> AuditDefinition:
    return AuditDefinition(
        action_key=_required_nonempty_str(action, "actionKey", "curated action is missing actionKey"),
        title=_required_nonempty_str(action, "title", "curated action is missing title"),
        metric_group=metric_group,
        runbook_kind=_required_nonempty_str(action, "runbookKind", "curated action is missing runbookKind"),
        runbook_id=_optional_str(action.get("fleetRunbookId")),
        artifact_schema_name=_optional_str(action.get("artifactSchemaName")),
        artifact_schema_version=_optional_str(action.get("artifactSchemaVersion")),
        task_description_template=_optional_str(action.get("taskDescriptionTemplate")),
    )


def _require_unique_action_keys(audits: tuple[AuditDefinition, ...]) -> None:
    action_keys = {audit.action_key for audit in audits}
    if len(action_keys) != len(audits):
        raise ValueError("catalog contains duplicate audit action keys")
    selectors = {audit.selector for audit in audits}
    if len(selectors) != len(audits):
        raise ValueError("catalog contains duplicate audit selectors")


def _curated_actions(payload: Mapping[str, object]) -> list[dict[str, object]]:
    actions = payload.get("curatedActions")
    if not isinstance(actions, list):
        return []
    if any(not isinstance(action, dict) for action in actions):
        raise ValueError("catalog curatedActions entries must be JSON objects")
    return [action for action in actions if isinstance(action, dict)]


def _json_object_list(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _required_nonempty_str(payload: dict[str, object], key: str, message: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(message)
    if not value.strip():
        raise ValueError(message)
    return value


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
