from enji_guard_cli.audits import AuditCatalog, AuditDefinition
from enji_guard_cli.core_impl.payloads import json_list, required_str
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

RECON_ACTION_KEY = "audit.recon"
AUDIT_CATEGORY = "audit"
PUBLISHED_STATUS = "published"


def parse_audit_catalog(payload: JsonObjectPayload) -> AuditCatalog:
    """Parse the current live audit catalog without retaining stale definitions."""

    actions = _curated_actions(payload)
    recon = _require_recon(actions)
    report_audits = tuple(_report_audit(action) for action in actions if _is_published_report_action(action))
    _require_unique_action_keys((recon, *report_audits))
    return AuditCatalog(report_audits=report_audits, recon=recon)


def _require_recon(actions: list[dict[str, JsonValue]]) -> AuditDefinition:
    recon_actions = [action for action in actions if action.get("actionKey") == RECON_ACTION_KEY]
    if len(recon_actions) != 1:
        raise ValueError(f"catalog must contain exactly one {RECON_ACTION_KEY} action")
    return _audit_definition(recon_actions[0], metric_group=None)


def _is_published_report_action(action: dict[str, JsonValue]) -> bool:
    return (
        action.get("actionKey") != RECON_ACTION_KEY
        and action.get("category") == AUDIT_CATEGORY
        and action.get("status") == PUBLISHED_STATUS
    )


def _report_audit(action: dict[str, JsonValue]) -> AuditDefinition:
    metric_group = _required_nonempty_str(action, "metricGroup", "published audit action is missing metricGroup")
    return _audit_definition(action, metric_group=metric_group)


def _audit_definition(action: dict[str, JsonValue], *, metric_group: str | None) -> AuditDefinition:
    return AuditDefinition(
        action_key=_required_nonempty_str(action, "actionKey", "curated action is missing actionKey"),
        title=_required_nonempty_str(action, "title", "curated action is missing title"),
        metric_group=metric_group,
        runbook_kind=_required_nonempty_str(action, "runbookKind", "curated action is missing runbookKind"),
    )


def _require_unique_action_keys(audits: tuple[AuditDefinition, ...]) -> None:
    action_keys = {audit.action_key for audit in audits}
    if len(action_keys) != len(audits):
        raise ValueError("catalog contains duplicate audit action keys")
    selectors = {audit.selector for audit in audits}
    if len(selectors) != len(audits):
        raise ValueError("catalog contains duplicate audit selectors")


def _curated_actions(payload: JsonObjectPayload) -> list[dict[str, JsonValue]]:
    actions = json_list(payload.get("curatedActions"))
    if any(not isinstance(action, dict) for action in actions):
        raise ValueError("catalog curatedActions entries must be JSON objects")
    return [action for action in actions if isinstance(action, dict)]


def _required_nonempty_str(payload: dict[str, JsonValue], key: str, message: str) -> str:
    value = required_str(payload, key, message)
    if not value.strip():
        raise ValueError(message)
    return value
