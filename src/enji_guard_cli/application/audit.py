"""Audit use-cases: catalog interpretation, starting, reading, and waiting."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from enji_guard_cli.application.catalog import AuditCatalogService
from enji_guard_cli.application.projects import AuditProjectSource
from enji_guard_cli.audit.artifacts import AuditRead, AuditSummary, summarize_artifacts
from enji_guard_cli.audit.errors import AuditNotFoundError
from enji_guard_cli.audit.lifecycle import is_active_run
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.observation import AuditRepositoryObserver
from enji_guard_cli.audit.ports import (
    AuditGatewayPort,
    AuditLedgerPort,
    AuditRunState,
    AuditStartOutcome,
    AuditStatus,
    AuditWaitOptions,
    AuditWaitResult,
)
from enji_guard_cli.audit.start import AuditStartService
from enji_guard_cli.audit.status import build_status
from enji_guard_cli.audit.wait import AuditWaitDependencies, wait_for_completion
from enji_guard_cli.audit.workflows import AuditWorkflowDependencies, choose_audits, read_for_repo
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.portfolio.ports import (
    AuditStartPort,
    AuditStatusReader,
    PortfolioAuditStatus,
    PortfolioTargetService,
)
from enji_guard_cli.settings import default_settings


@dataclass(frozen=True, slots=True)
class AuditReconService(AuditStatusReader, AuditStartPort):
    """Audit-side implementation of the two ports Portfolio recon declares.

    It is built from Audit collaborators and one frozen catalog, so Portfolio
    use-cases depend on a typed port rather than on another facade.
    """

    catalog: AuditCatalog
    observer: AuditRepositoryObserver
    start_service: AuditStartService

    def status(self, repo_id: str) -> PortfolioAuditStatus:
        observation = self.observer.observe(repo_id)
        status = build_status(
            repo_id,
            self.catalog,
            observation.task_links,
            observation.active_runs,
            observation.rerun_state,
        )
        active_runs = tuple(run for run in observation.active_runs if is_active_run(run))
        return PortfolioAuditStatus.from_audit_status(status, active_runs=active_runs)

    def start(self, repo_id: str, project_id: str, action_key: str) -> AuditStartOutcome:
        audit = audit_for_action(self.catalog, action_key)
        results = cast(
            list[dict[str, object]],
            self.start_service.start(repo_id, project_id, (audit,), self.catalog)["results"],
        )
        return _start_outcome(results[0])


class AuditReconFactory(Protocol):
    """Seam Portfolio depends on when a use-case needs live audit state."""

    def recon(self, catalog: AuditCatalog) -> AuditReconService: ...


@dataclass(frozen=True, slots=True)
class AuditFacade:
    """Start, read, summarize, and wait on repository audits."""

    catalog: AuditCatalogService
    gateway: AuditGatewayPort
    ledger: AuditLedgerPort
    targets: PortfolioTargetService
    project_source: AuditProjectSource
    fanout: BoundedFanout

    def recon(self, catalog: AuditCatalog) -> AuditReconService:
        """Bind Audit collaborators to one frozen catalog for Portfolio's ports."""
        return AuditReconService(catalog, self.observer(), self.start_service())

    def observer(self) -> AuditRepositoryObserver:
        return AuditRepositoryObserver(self.gateway, self.ledger, self.fanout)

    def start_service(self) -> AuditStartService:
        return AuditStartService(self.gateway, self.ledger, self.project_source)

    def audit_status(self, repo_id: str, *, catalog: AuditCatalog | None = None) -> AuditStatus:
        frozen = catalog if catalog is not None else self.catalog.audits()
        return self.recon(frozen).status(repo_id).summary

    def audit_start(
        self, repo: str, project: str | None = None, selectors: list[str] | None = None, *, all_audits: bool = False
    ) -> object:
        target = self.targets.resolve_repository(repo, project=project)
        catalog = self.catalog.audits()
        selected = choose_audits(catalog, selectors or [], all_audits=all_audits)
        batch = self.start_service().start(target.repo_id, target.project_id, selected, catalog)
        return {"repo_id": target.repo_id, "project_id": target.project_id, **batch}

    def audit_read(
        self, repo: str, selectors: list[str] | None = None, *, project: str | None = None, all_audits: bool = False
    ) -> AuditRead:
        target = self.targets.resolve_repository(repo, project=project)
        items = read_for_repo(
            target.repo_id, selectors or [], all_audits=all_audits, dependencies=self._workflow_dependencies()
        )
        return AuditRead(target.repo_id, items)

    def audit_summary(
        self, repo: str, selectors: list[str] | None = None, *, project: str | None = None
    ) -> AuditSummary:
        target = self.targets.resolve_repository(repo, project=project)
        items = read_for_repo(
            target.repo_id,
            selectors or [],
            all_audits=not bool(selectors),
            dependencies=self._workflow_dependencies(),
        )
        return summarize_artifacts(target.repo_id, items)

    def audit_wait(
        self,
        repo: str,
        *,
        project: str | None = None,
        timeout_seconds: float | None = None,
        heartbeat: Callable[[AuditWaitResult], None] | None = None,
    ) -> AuditWaitResult:
        target = self.targets.resolve_repository(repo, project=project)
        settings = default_settings().audit_wait
        options = AuditWaitOptions(
            settings.poll_seconds,
            settings.timeout_seconds if timeout_seconds is None else timeout_seconds,
            settings.heartbeat_seconds,
        )
        catalog = self.catalog.audits()
        return wait_for_completion(
            target.repo_id,
            options=options,
            heartbeat=heartbeat,
            dependencies=AuditWaitDependencies(
                lambda repo_id: self.audit_status(repo_id, catalog=catalog), time.monotonic, time.sleep
            ),
        )

    def _workflow_dependencies(self) -> AuditWorkflowDependencies:
        """Freeze the catalog once so one command makes exactly one catalog call."""
        return AuditWorkflowDependencies(
            catalog=self.gateway,
            gateway=self.gateway,
            project=self.project_source,
            frozen_catalog=self.catalog.audits(),
            repository_observation=self.observer().observe,
        )


def audit_for_action(catalog: AuditCatalog, action_key: str) -> AuditDefinition:
    """Resolve one action key against the catalog that is live right now."""
    if action_key == catalog.recon.action_key:
        return catalog.recon
    audit = next((item for item in catalog.published_audits if item.action_key == action_key), None)
    if audit is None:
        raise AuditNotFoundError(f"audit action is no longer published: {action_key}")
    return audit


def _start_outcome(item: dict[str, object]) -> AuditStartOutcome:
    """Carry the batch item's own verdict across the port, unrewritten."""
    return AuditStartOutcome(
        cast(AuditRunState, item["state"]),
        cast(str | None, item.get("task_id")),
        cast(str | None, item.get("task_status")),
        cast(str | None, item.get("reason")),
    )


__all__ = ["AuditFacade", "AuditReconFactory", "AuditReconService", "audit_for_action"]
