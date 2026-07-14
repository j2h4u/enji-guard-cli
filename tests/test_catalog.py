import pytest

from enji_guard_cli.audits import AuditCatalog, AuditDefinition
from enji_guard_cli.core_impl.audit_runs import selected_audits
from enji_guard_cli.core_impl.catalog import parse_audit_catalog
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue


def test_parse_audit_catalog_selects_live_published_audits_and_recon() -> None:
    catalog = parse_audit_catalog(
        _catalog(
            [
                _action("audit.recon", title="Recon", category="workflow", status="draft", runbook_kind="recon"),
                _action(
                    "audit.security",
                    title="Security",
                    category="audit",
                    status="published",
                    metric_group="vulns",
                    runbook_kind="vuln-audit",
                ),
                _action(
                    "audit.cicd",
                    title="CI/CD",
                    category="audit",
                    status="published",
                    metric_group="cicd",
                    runbook_kind="cicd-audit",
                ),
                _action(
                    "audit.draft",
                    title="Draft",
                    category="audit",
                    status="draft",
                    metric_group="drafts",
                    runbook_kind="draft-audit",
                ),
                _action(
                    "autofix.security",
                    title="Fix security",
                    category="autofix",
                    status="published",
                    metric_group="vulns",
                    runbook_kind="vuln-fix",
                ),
            ]
        )
    )

    assert isinstance(catalog, AuditCatalog)
    assert catalog.recon == AuditDefinition(
        action_key="audit.recon",
        title="Recon",
        metric_group=None,
        runbook_kind="recon",
    )
    assert catalog.published_audits == (
        AuditDefinition(
            action_key="audit.security",
            title="Security",
            metric_group="vulns",
            runbook_kind="vuln-audit",
        ),
        AuditDefinition(
            action_key="audit.cicd",
            title="CI/CD",
            metric_group="cicd",
            runbook_kind="cicd-audit",
        ),
    )
    assert [audit.selector for audit in catalog.published_audits] == ["security", "cicd"]


def test_parse_audit_catalog_requires_a_live_recon_action() -> None:
    with pytest.raises(ValueError, match=r"audit\.recon"):
        parse_audit_catalog({"curatedActions": []})


def test_parse_audit_catalog_rejects_duplicate_action_keys() -> None:
    with pytest.raises(ValueError, match="duplicate audit action keys"):
        parse_audit_catalog(
            _catalog(
                [
                    _action(
                        "audit.recon", title="Recon", category="workflow", status="published", runbook_kind="recon"
                    ),
                    _action(
                        "audit.security",
                        title="Security",
                        category="audit",
                        status="published",
                        metric_group="vulns",
                        runbook_kind="vuln-audit",
                    ),
                    _action(
                        "audit.security",
                        title="Security duplicate",
                        category="audit",
                        status="published",
                        metric_group="vulns",
                        runbook_kind="vuln-audit",
                    ),
                ]
            )
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("actionKey", None, "curated action is missing actionKey"),
        ("actionKey", "", "curated action is missing actionKey"),
        ("title", None, "curated action is missing title"),
        ("title", "", "curated action is missing title"),
        ("metricGroup", None, "published audit action is missing metricGroup"),
        ("metricGroup", "", "published audit action is missing metricGroup"),
        ("runbookKind", None, "curated action is missing runbookKind"),
        ("runbookKind", "", "curated action is missing runbookKind"),
    ],
)
def test_parse_audit_catalog_rejects_malformed_published_audit_actions(
    field: str,
    value: JsonValue,
    message: str,
) -> None:
    action = _action(
        "audit.security",
        title="Security",
        category="audit",
        status="published",
        metric_group="vulns",
        runbook_kind="vuln-audit",
    )
    action[field] = value

    with pytest.raises(ValueError, match=message):
        parse_audit_catalog(
            _catalog(
                [
                    _action(
                        "audit.recon", title="Recon", category="workflow", status="published", runbook_kind="recon"
                    ),
                    action,
                ]
            )
        )


def test_parse_audit_catalog_rejects_non_object_curated_action() -> None:
    payload = _catalog(
        [_action("audit.recon", title="Recon", category="workflow", status="published", runbook_kind="recon")]
    )
    payload["curatedActions"] = ["not-an-action"]

    with pytest.raises(ValueError, match="curatedActions entries must be JSON objects"):
        parse_audit_catalog(payload)


def test_audit_selector_resolution_rejects_full_keys_and_legacy_aliases() -> None:
    catalog = parse_audit_catalog(
        _catalog(
            [
                _action("audit.recon", title="Recon", category="workflow", status="published", runbook_kind="recon"),
                _action(
                    "audit.security",
                    title="Security",
                    category="audit",
                    status="published",
                    metric_group="vulns",
                    runbook_kind="vuln-audit",
                ),
                _action(
                    "audit.cicd",
                    title="CI/CD",
                    category="audit",
                    status="published",
                    metric_group="cicd",
                    runbook_kind="cicd-audit",
                ),
                _action(
                    "audit.dependency-hygiene",
                    title="Dependency hygiene",
                    category="audit",
                    status="published",
                    metric_group="dependency-hygiene",
                    runbook_kind="dependency-hygiene-audit",
                ),
            ]
        )
    )

    assert [
        audit.action_key
        for audit in selected_audits(["security", "cicd", "dependency-hygiene"], all_reports=False, catalog=catalog)
    ] == ["audit.security", "audit.cicd", "audit.dependency-hygiene"]
    with pytest.raises(ValueError, match="unknown audit selector: deps"):
        selected_audits(["deps"], all_reports=False, catalog=catalog)
    with pytest.raises(ValueError, match=r"unknown audit selector: audit\.security"):
        selected_audits(["audit.security"], all_reports=False, catalog=catalog)


def _action(
    action_key: str,
    **fields: str | None,
) -> JsonObjectPayload:
    wire_names = {"metric_group": "metricGroup", "runbook_kind": "runbookKind"}
    return {"actionKey": action_key, **{wire_names.get(name, name): value for name, value in fields.items()}}


def _catalog(actions: list[JsonObjectPayload]) -> JsonObjectPayload:
    curated_actions: list[JsonValue] = []
    curated_actions.extend(actions)
    return {"curatedActions": curated_actions}
