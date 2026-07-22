from typing import cast

from enji_guard_cli.audit.observation import AuditRepositoryObserver
from enji_guard_cli.audit.ports import (
    AuditGatewayPort,
    AuditLedgerPort,
    AuditRerunState,
    AuditRun,
    AuditRunsResult,
    AuditTaskDetail,
    AuditTaskLink,
    AuditTaskLinksResult,
)
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.settings import FanoutSettings


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def task_links(self, repo_id: str) -> AuditTaskLinksResult:
        self.calls.append(f"links:{repo_id}")
        return AuditTaskLinksResult((AuditTaskLink("task-1", "audit.security", "completed"),))

    def active_runs(self, repo_id: str) -> AuditRunsResult:
        self.calls.append(f"runs:{repo_id}")
        return AuditRunsResult((AuditRun("task-2", "audit.tests", "running", None, None, None),))

    def rerun_state(self, repo_id: str) -> AuditRerunState:
        self.calls.append(f"rerun:{repo_id}")
        return AuditRerunState("head", "head", False, "task-1")

    def task_detail(self, task_id: str) -> AuditTaskDetail:
        raise AssertionError(task_id)


class _Ledger:
    def __init__(self) -> None:
        self.reconciled = False

    def reconcile(self, repo_id: str, upstream: tuple[AuditRun, ...], task_lookup: object) -> tuple[AuditRun, ...]:
        self.reconciled = True
        return upstream


def test_repository_observer_loads_three_projections_and_reconciles_runs() -> None:
    gateway = _Gateway()
    ledger = _Ledger()
    observation = AuditRepositoryObserver(
        cast(AuditGatewayPort, gateway),
        cast(AuditLedgerPort, ledger),
        BoundedFanout(FanoutSettings(max_concurrency=3)),
    ).observe("repo-1")

    assert [link.action_key for link in observation.task_links] == ["audit.security"]
    assert [run.action_key for run in observation.active_runs] == ["audit.tests"]
    assert observation.rerun_state.current_head_sha == "head"
    assert ledger.reconciled
    assert sorted(gateway.calls) == ["links:repo-1", "rerun:repo-1", "runs:repo-1"]
