import pytest

from enji_guard_cli.audit.catalog import parse_catalog_result
from enji_guard_cli.audit.ports import AuditCatalogAction, AuditCatalogResult
from enji_guard_cli.audit.runs import selected_audits


def _action(
    action_key: str,
    **fields: str | None,
) -> AuditCatalogAction:
    return AuditCatalogAction(
        action_key,
        fields["title"] or "",
        fields.get("category"),
        fields.get("status"),
        fields.get("metric_group"),
        fields.get("runbook_kind"),
    )


def _catalog(*actions: AuditCatalogAction) -> AuditCatalogResult:
    return AuditCatalogResult(actions=actions, autofixes=())


def test_catalog_selects_published_audits_and_recon() -> None:
    catalog = parse_catalog_result(
        _catalog(
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
                "audit.draft",
                title="Draft",
                category="audit",
                status="draft",
                metric_group="draft",
                runbook_kind="draft-audit",
            ),
        )
    )

    assert catalog.recon.action_key == "audit.recon"
    assert [(audit.action_key, audit.metric_group) for audit in catalog.published_audits] == [
        ("audit.security", "vulns")
    ]


def test_catalog_requires_one_recon_action() -> None:
    with pytest.raises(ValueError, match=r"exactly one audit\.recon"):
        parse_catalog_result(_catalog())


def test_catalog_validates_required_domain_fields() -> None:
    with pytest.raises(ValueError, match="metric group"):
        parse_catalog_result(
            _catalog(
                _action("audit.recon", title="Recon", category="workflow", status="draft", runbook_kind="recon"),
                _action(
                    "audit.security",
                    title="Security",
                    category="audit",
                    status="published",
                    runbook_kind="vuln-audit",
                ),
            )
        )


def test_catalog_rejects_duplicate_action_keys() -> None:
    with pytest.raises(ValueError, match="duplicate audit action keys"):
        parse_catalog_result(
            _catalog(
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
                    "audit.security",
                    title="Security 2",
                    category="audit",
                    status="published",
                    metric_group="vulns",
                    runbook_kind="vuln-audit",
                ),
            )
        )


def test_audit_selector_resolution_uses_catalog_suffixes() -> None:
    catalog = parse_catalog_result(
        _catalog(
            _action("audit.recon", title="Recon", category="workflow", status="draft", runbook_kind="recon"),
            _action(
                "audit.security",
                title="Security",
                category="audit",
                status="published",
                metric_group="vulns",
                runbook_kind="vuln-audit",
            ),
        )
    )

    assert selected_audits(["security"], all_audits=False, catalog=catalog) == list(catalog.published_audits)
