from enji_guard_cli.audit.artifacts import (
    ArtifactReadItem,
    AuditArtifactUnavailableError,
    read_artifacts,
    select_artifacts,
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


def test_read_artifacts_returns_ready_artifact_and_tolerated_missing() -> None:
    result = read_artifacts(
        "repo",
        (_item(),),
        reader=lambda _repo, _key: AuditArtifact("audit.security", "body"),
        tolerate_unavailable=False,
    )
    assert result == (
        ArtifactReadItem("audit.security", True, AuditArtifact("audit.security", "body"), None, _item().freshness),
    )


def test_explicit_unreadable_selection_is_rejected() -> None:
    try:
        select_artifacts((_item(False),), ["security"], all_artifacts=False, catalog=_catalog())
    except AuditArtifactUnavailableError as exc:
        assert exc.audit_key == "audit.security"
    else:
        raise AssertionError("expected unreadable selection to fail")
