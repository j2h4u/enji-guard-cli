from collections.abc import Callable
from typing import cast

import pytest

from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import AuditCatalogAction, AuditCatalogResult, AuditGatewayPort, AuditProject
from enji_guard_cli.audit.workflows import AuditWorkflowDependencies, _catalog, choose_audits


def _catalog_model() -> AuditCatalog:
    return AuditCatalog(
        published_audits=(AuditDefinition("audit.security", "Security", "vulns", "security"),),
        recon=AuditDefinition("audit.recon", "Recon", None, "recon"),
    )


def test_choose_audits_supports_all_and_selectors() -> None:
    catalog = _catalog_model()
    assert choose_audits(catalog, [], all_audits=True) == catalog.published_audits
    assert choose_audits(catalog, ["security"], all_audits=False) == catalog.published_audits


@pytest.mark.parametrize(
    ("selectors", "all_audits", "message"),
    [([], False, "at least one"), (["security"], True, "not both"), (["missing"], False, "unknown")],
)
def test_choose_audits_rejects_invalid_selection(selectors: list[str], all_audits: bool, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        choose_audits(_catalog_model(), selectors, all_audits=all_audits)


class _CatalogPort:
    def __init__(self, result: AuditCatalogResult) -> None:
        self.result = result
        self.calls = 0

    def catalog(self) -> AuditCatalogResult:
        self.calls += 1
        return self.result


def test_catalog_converts_live_actions_and_rejects_missing_recon() -> None:
    port = _CatalogPort(
        AuditCatalogResult(
            actions=(
                AuditCatalogAction("audit.recon", "Recon", "audit", "published", None, "recon"),
                AuditCatalogAction("audit.security", "Security", "audit", "published", "vulns", "security"),
                AuditCatalogAction("audit.hidden", "Hidden", "audit", "draft", None, None),
            )
        )
    )
    dependencies = AuditWorkflowDependencies(
        port, cast(AuditGatewayPort, object()), cast(Callable[[str], AuditProject], lambda _project: object())
    )
    result = _catalog(dependencies)
    assert [item.action_key for item in result.published_audits] == ["audit.security"]
    assert port.calls == 1

    missing = _CatalogPort(AuditCatalogResult(actions=()))
    with pytest.raises(ValueError, match=r"audit\.recon"):
        _catalog(
            AuditWorkflowDependencies(
                missing,
                cast(AuditGatewayPort, object()),
                cast(Callable[[str], AuditProject], lambda _project: object()),
            )
        )


def test_catalog_uses_frozen_observation_without_fetch() -> None:
    port = _CatalogPort(AuditCatalogResult(actions=()))
    frozen = _catalog_model()
    assert (
        _catalog(
            AuditWorkflowDependencies(
                port,
                cast(AuditGatewayPort, object()),
                cast(Callable[[str], AuditProject], lambda _project: object()),
                frozen,
            )
        )
        is frozen
    )
    assert port.calls == 0
