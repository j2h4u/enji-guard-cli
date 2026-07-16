"""Repository recon workflow owned by Portfolio.

Only the action identity and typed start/status ports cross this boundary; task
construction and lifecycle remain in Audit.
"""

from dataclasses import dataclass

from enji_guard_cli.portfolio.models import RepositoryRef
from enji_guard_cli.portfolio.ports import AuditStartPort, AuditStatusReader

RECON_ACTION_KEY = "audit.recon"


@dataclass(frozen=True, slots=True)
class ReconResult:
    state: str
    repo_id: str
    task_id: str | None = None
    task_status: str | None = None


def start_recon(repository: RepositoryRef, *, audits: AuditStatusReader, starter: AuditStartPort) -> ReconResult:
    """Start recon only when baseline diagnostics are not already ready."""

    status = audits.status(repository.repo_id)
    for run in status.active_runs:
        if run.action_key == RECON_ACTION_KEY:
            return ReconResult("already_running", repository.repo_id, run.task_id, run.status)
    if repository.recon_done is True:
        return ReconResult("unchanged", repository.repo_id)
    result = starter.start(repository.repo_id, repository.project_id, RECON_ACTION_KEY)
    return ReconResult("started", repository.repo_id, result.task_id, result.status)


def recon_after_add(repository: RepositoryRef, *, audits: AuditStatusReader, starter: AuditStartPort) -> ReconResult:
    """The ``repo add`` continuation: existing membership still enters recon."""

    return start_recon(repository, audits=audits, starter=starter)


run_recon = start_recon
