from enji_guard_cli.audit.catalog import parse_audit_catalog


def test_catalog_preserves_metric_group_and_rejects_duplicate_keys() -> None:
    base = {"category": "audit", "status": "published", "runbookKind": "audit"}
    catalog = parse_audit_catalog(
        {
            "curatedActions": [
                {
                    **base,
                    "actionKey": "audit.recon",
                    "title": "Recon",
                    "category": "workflow",
                    "status": "draft",
                    "runbookKind": "recon",
                },
                {**base, "actionKey": "audit.security", "title": "Security", "metricGroup": "vulns"},
            ]
        }
    )
    assert catalog.published_audits[0].metric_group == "vulns"
    try:
        parse_audit_catalog({"curatedActions": [{"actionKey": "audit.x"}, {"actionKey": "audit.x"}]})
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate catalog keys must be rejected")
