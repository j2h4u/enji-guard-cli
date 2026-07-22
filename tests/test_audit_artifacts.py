from enji_guard_cli.audit.artifacts import (
    ArtifactReadItem,
    select_artifacts,
    summarize_artifacts,
)
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import AuditArtifact, AuditFreshness, AuditStatusItem


def _catalog() -> AuditCatalog:
    return AuditCatalog(
        published_audits=(AuditDefinition("audit.security", "Security", "vulns", "security"),),
        recon=AuditDefinition("audit.recon", "Recon", None, "recon"),
    )


def _item(readable: bool = True) -> AuditStatusItem:
    return AuditStatusItem(
        "audit.security", "Security", AuditFreshness("a", "a", "fresh"), readable, "none", None, None
    )


def test_select_uses_suffix_and_tolerates_unselected_missing() -> None:
    assert (
        select_artifacts((_item(),), ["security"], all_artifacts=False, catalog=_catalog())[0].audit_key
        == "audit.security"
    )


def test_summary_discards_report_body_at_audit_boundary() -> None:
    items = (
        ArtifactReadItem(
            "audit.security",
            True,
            AuditArtifact("audit.security", "very large report", 73, "2026-07-20T00:00:00Z"),
            None,
            _item().freshness,
        ),
    )

    summary = summarize_artifacts("repo", items)

    assert summary.repo_id == "repo"
    assert summary.audits[0].score == 73
    assert not hasattr(summary.audits[0], "body")


def test_explicit_selection_can_read_history_for_currently_unreadable_status() -> None:
    selected = select_artifacts((_item(False),), ["security"], all_artifacts=False, catalog=_catalog())
    assert selected[0].audit_key == "audit.security"
