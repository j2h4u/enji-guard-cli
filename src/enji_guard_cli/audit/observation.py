"""Concurrent, typed read projections for one Audit repository."""

from dataclasses import dataclass

from enji_guard_cli.audit.ports import AuditGatewayPort, AuditLedgerPort, AuditRerunState, AuditRun, AuditTaskLink
from enji_guard_cli.fanout import BoundedFanout, IndependentRead


@dataclass(frozen=True, slots=True)
class AuditRepositoryObservation:
    """The independent projections needed to build repository audit status."""

    task_links: tuple[AuditTaskLink, ...]
    active_runs: tuple[AuditRun, ...]
    rerun_state: AuditRerunState


class AuditRepositoryObserver:
    """Load repository projections concurrently while preserving ledger semantics."""

    def __init__(
        self,
        gateway: AuditGatewayPort,
        ledger: AuditLedgerPort | None,
        fanout: BoundedFanout,
    ) -> None:
        self.gateway = gateway
        self.ledger = ledger
        self.fanout = fanout

    def observe(self, repo_id: str) -> AuditRepositoryObservation:
        task_links, active_runs, rerun_state = self.fanout.gather(
            IndependentRead(lambda: self.gateway.task_links(repo_id).links),
            IndependentRead(lambda: self._active_runs(repo_id)),
            IndependentRead(lambda: self.gateway.rerun_state(repo_id)),
        )
        return AuditRepositoryObservation(task_links, active_runs, rerun_state)

    load = observe

    def _active_runs(self, repo_id: str) -> tuple[AuditRun, ...]:
        upstream = self.gateway.active_runs(repo_id).runs
        if self.ledger is None:
            return upstream
        return self.ledger.reconcile(repo_id, upstream, self.gateway.task_detail)


AuditRepositoryObservationLoader = AuditRepositoryObserver


__all__ = [
    "AuditRepositoryObservation",
    "AuditRepositoryObservationLoader",
    "AuditRepositoryObserver",
]
