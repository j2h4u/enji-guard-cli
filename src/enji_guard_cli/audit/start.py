"""Audit start workflow: duplicate guards and ledger recording."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from enji_guard_cli.audit.ledger import new_entry
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import (
    AuditGatewayPort,
    AuditLedgerPort,
    AuditProject,
    AuditRunRequest,
    AuditRunResult,
    AuditRunStart,
)
from enji_guard_cli.audit.runs import (
    StartAuditDependencies,
    StartAuditsContext,
    start_audits_for_target,
)


class AuditStartService:
    """Own all stateful concerns around starting one or more audits."""

    def __init__(
        self,
        gateway: AuditGatewayPort,
        ledger: AuditLedgerPort | None,
        project: Callable[[str], AuditProject],
    ) -> None:
        self.gateway = gateway
        self.ledger = ledger
        self.project = project

    def active_runs(self, repo_id: str):
        upstream = self.gateway.active_runs(repo_id).runs
        if self.ledger is None:
            return upstream
        return self.ledger.reconcile(repo_id, upstream, self.gateway.task_detail)

    def start(
        self, repo_id: str, project_id: str, audits: tuple[AuditDefinition, ...], catalog: AuditCatalog
    ) -> dict[str, object]:
        dependencies = StartAuditDependencies(
            make_audit_run_create=lambda target_repo, target_project, action_key, body: AuditRunRequest(
                target_repo, target_project, action_key, body
            ),
            start_audit_run=self.gateway.start_audit_run,
            project_detail=self.project,
            runbook=lambda runbook_id: self.gateway.runbook_metadata(runbook_id),
            current_repo_active_runs=self.active_runs,
            record_started_run=self._record_started,
            task_identity=lambda response: (
                cast(AuditRunResult, response).task_id,
                cast(AuditRunResult, response).status,
            ),
        )
        return start_audits_for_target(
            StartAuditsContext(repo_id, project_id, list(audits), catalog),
            dependencies=dependencies,
            get_repo_rerun_state=self.gateway.rerun_state,
        )

    def _record_started(self, context: AuditRunStart) -> None:
        if self.ledger is None:
            return
        self.ledger.record_started(
            new_entry(
                context,
                observed_at=datetime.now(UTC),
                ttl_seconds=getattr(self.ledger, "ttl_seconds", 21_600),
            )
        )


__all__ = ["AuditStartService"]
