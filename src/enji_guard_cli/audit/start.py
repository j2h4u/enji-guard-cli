"""Audit start workflow: snapshot preservation, duplicate guards, and ledger."""

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
)
from enji_guard_cli.audit.runs import (
    RecordStartedRunContext,
    StartAuditDependencies,
    StartAuditsContext,
    start_audits_for_target,
)
from enji_guard_cli.audit.status import build_status


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
        self._preserve_snapshots(repo_id, catalog)
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

    def _preserve_snapshots(self, repo_id: str, catalog: AuditCatalog) -> None:
        status = build_status(
            repo_id,
            catalog,
            self.gateway.task_links(repo_id).links,
            self.active_runs(repo_id),
            self.gateway.rerun_state(repo_id),
        )
        groups = {audit.action_key: audit.metric_group for audit in catalog.published_audits}
        for action_key in status.readable:
            self.gateway.read_audit_snapshot(repo_id, action_key, groups.get(action_key))

    def _record_started(self, context: RecordStartedRunContext) -> None:
        if self.ledger is None:
            return
        self.ledger.record_started(
            new_entry(
                repo_id=context.repo_id,
                project_id=context.project_id,
                audit_key=context.action_key,
                task_id=context.task_id,
                task_status=context.task_status,
                current_head_sha=context.current_head_sha,
                audited_head_sha=context.last_audited_head_sha,
                observed_at=datetime.now(UTC),
                ttl_seconds=getattr(self.ledger, "ttl_seconds", 21_600),
            )
        )


__all__ = ["AuditStartService"]
