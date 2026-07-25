"""One live catalog fetch, observed once and shared by the Audit facades."""

from dataclasses import dataclass

from enji_guard_cli.application.execution import CatalogObservationScope
from enji_guard_cli.audit import parse_catalog_result
from enji_guard_cli.audit.catalog_observation import AuditCatalogObservationPort
from enji_guard_cli.audit.models import AuditCatalog
from enji_guard_cli.audit.ports import AuditCatalogResult, AuditGatewayPort


@dataclass(frozen=True, slots=True)
class AuditCatalogService:
    """Fetch the upstream catalog and publish its observation to the runner."""

    gateway: AuditGatewayPort
    observer: AuditCatalogObservationPort
    scope: CatalogObservationScope

    def catalog(self) -> AuditCatalogResult:
        """Fetch the live catalog once; ``changes`` is the typed observation hook."""
        result = self.observer.observe(self.gateway.catalog())
        self.scope.record(result)
        return result

    def audits(self) -> AuditCatalog:
        """Return the interpreted catalog that use-cases freeze for one command."""
        return parse_catalog_result(self.catalog())


__all__ = ["AuditCatalogService"]
