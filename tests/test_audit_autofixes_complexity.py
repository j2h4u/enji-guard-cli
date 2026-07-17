import pytest

from enji_guard_cli.audit.autofixes import definitions, desired_job, select, set_one
from enji_guard_cli.audit.ports import (
    AuditAutofixDefinition,
    AuditAutofixUpdate,
    AuditCatalogAction,
    AuditCatalogAutofix,
    AuditCatalogResult,
)


def _definition(*, supported: bool = True) -> AuditAutofixDefinition:
    return AuditAutofixDefinition(
        "improvement.vuln-fix", "default", "Vuln fix", None, "audit.security", "vuln-fix", supported
    )


def _catalog() -> AuditCatalogResult:
    return AuditCatalogResult(
        actions=(AuditCatalogAction("audit.security", "Security", "audit", "published", "vulns", "security"),),
        autofixes=(
            AuditCatalogAutofix("improvement.vuln-fix", "default", "Vuln fix", status="published"),
            AuditCatalogAutofix("improvement.pentest", "default", "Pentest", status="published"),
            AuditCatalogAutofix("improvement.vuln-fix", "hidden", "Hidden", status="draft"),
        ),
    )


def test_definitions_exclude_separate_and_unpublished_catalog_entries() -> None:
    result = definitions(_catalog())
    assert [item.variant_key for item in result] == ["default"]
    assert result[0].supported is True


def test_select_supports_all_and_deduplicates() -> None:
    available = (_definition(),)
    assert select(["__all__"], available) == available
    assert select(["vuln-fix", "vuln-fix"], available) == available


@pytest.mark.parametrize(
    ("selectors", "message"),
    [(["missing"], "unknown autofix selector"), (["vuln-fix"], "unsupported")],
)
def test_select_rejects_invalid_or_unsupported(selectors: list[str], message: str) -> None:
    available = (_definition(supported=selectors == ["missing"]),)
    with pytest.raises(ValueError, match=message):
        select(selectors, available)


def test_desired_job_requires_timezone_for_new_enable_and_preserves_defaults() -> None:
    with pytest.raises(ValueError, match="timezone"):
        desired_job(
            None,
            _definition(),
            AuditAutofixUpdate(True),
        )
    result = desired_job(None, _definition(), AuditAutofixUpdate(True, timezone="UTC"))
    assert result is not None
    assert result.enabled is True
    assert result.days_of_week == ("mon", "tue", "wed", "thu", "fri")


def test_set_one_is_idempotent_and_skips_new_disable() -> None:
    definition = _definition()
    assert (
        set_one(definition, None, AuditAutofixUpdate(False), lambda *_: pytest.fail("must not write")).status
        == "unchanged"
    )
    existing = desired_job(None, definition, AuditAutofixUpdate(True, timezone="UTC"))
    assert existing is not None
    assert existing is not None
    result = set_one(definition, existing, AuditAutofixUpdate(True), lambda *_: pytest.fail("must not write"))
    assert result.status == "unchanged"
