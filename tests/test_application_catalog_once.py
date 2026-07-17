import pytest

from enji_guard_cli.audit.catalog import parse_catalog_result
from enji_guard_cli.audit.ports import AuditCatalogAction, AuditCatalogResult


def test_catalog_preserves_metric_group_and_rejects_duplicate_keys() -> None:
    recon = AuditCatalogAction("audit.recon", "Recon", "workflow", "draft", None, "recon")
    security = AuditCatalogAction("audit.security", "Security", "audit", "published", "vulns", "audit")
    catalog = parse_catalog_result(AuditCatalogResult(actions=(recon, security), autofixes=()))
    assert catalog.published_audits[0].metric_group == "vulns"

    duplicate = AuditCatalogAction("audit.security", "Duplicate", "audit", "published", "vulns", "audit")
    with pytest.raises(ValueError, match="duplicate"):
        parse_catalog_result(AuditCatalogResult(actions=(recon, security, duplicate), autofixes=()))
