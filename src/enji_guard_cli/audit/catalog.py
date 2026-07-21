from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import AuditCatalogAction, AuditCatalogResult

RECON_ACTION_KEY = "audit.recon"
AUDIT_CATEGORY = "audit"
PUBLISHED_STATUS = "published"


def parse_catalog_result(result: AuditCatalogResult) -> AuditCatalog:
    """Validate typed gateway data and build the Audit domain catalog."""

    recon_actions = tuple(action for action in result.actions if action.action_key == RECON_ACTION_KEY)
    if len(recon_actions) != 1:
        raise ValueError(f"catalog must contain exactly one {RECON_ACTION_KEY} action")
    recon = _audit_definition(recon_actions[0], metric_group=None)
    published_audits = tuple(
        _audit_definition(
            action, metric_group=_required(action.metric_group, "published audit action is missing metric group")
        )
        for action in result.actions
        if action.action_key != RECON_ACTION_KEY
        and action.category == AUDIT_CATEGORY
        and action.status == PUBLISHED_STATUS
    )
    _require_unique_action_keys((recon, *published_audits))
    return AuditCatalog(published_audits=published_audits, recon=recon)


def _audit_definition(action: AuditCatalogAction, *, metric_group: str | None) -> AuditDefinition:
    return AuditDefinition(
        action_key=_required(action.action_key, "curated action is missing action key"),
        title=_required(action.title, "curated action is missing title"),
        metric_group=metric_group,
        runbook_kind=_required(action.runbook_kind, "curated action is missing runbook kind"),
        runbook_id=action.runbook_id,
        artifact_schema_name=action.artifact_schema_name,
        artifact_schema_version=action.artifact_schema_version,
        task_description_template=action.task_description_template,
    )


def _required(value: str | None, message: str) -> str:
    if value is None or not value.strip():
        raise ValueError(message)
    return value


def _require_unique_action_keys(audits: tuple[AuditDefinition, ...]) -> None:
    action_keys = {audit.action_key for audit in audits}
    if len(action_keys) != len(audits):
        raise ValueError("catalog contains duplicate audit action keys")
    selectors = {audit.selector for audit in audits}
    if len(selectors) != len(audits):
        raise ValueError("catalog contains duplicate audit selectors")
