"""Application ports for Portfolio and its narrow Audit dependency."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from enji_guard_cli.audit.ports import AuditRun, AuditRunState, AuditStartOutcome, AuditStatus
from enji_guard_cli.portfolio.models import (
    AccessInfo,
    AccountPreferences,
    MovePreflight,
    PortfolioActiveRun,
    ProjectDetail,
    ProjectRef,
    RepositoryIdentity,
    RepositoryRef,
)


class PortfolioGatewayPort(Protocol):
    """Typed project/repository operations supplied by an infrastructure adapter."""

    def list_projects(self) -> tuple[ProjectRef, ...]: ...
    def project_detail(self, project_id: str) -> ProjectDetail: ...
    def project_active_runs(self, project_id: str) -> tuple[PortfolioActiveRun, ...]: ...
    def create_project(self, name: str) -> ProjectRef: ...
    def rename_project(self, project_id: str, name: str) -> ProjectRef: ...
    def delete_project(self, project_id: str) -> None: ...
    def add_repository(
        self, project_id: str, identity: RepositoryIdentity, repo_access_credential_id: str | None = None
    ) -> RepositoryRef: ...
    def remove_repository(self, project_id: str, repo_id: str) -> None: ...
    def connect_repository(self, project_id: str, repo_id: str) -> RepositoryRef: ...
    def preflight_repository_move(
        self, source_project_id: str, repo_id: str, target_project_id: str
    ) -> MovePreflight: ...
    def move_repository(self, source_project_id: str, repo_id: str, target_project_id: str) -> RepositoryRef: ...

    def get_preferences(self) -> AccountPreferences: ...
    def set_preferences(self, preferences: AccountPreferences) -> AccountPreferences: ...
    def access(self) -> AccessInfo: ...


class SelectorResolver(Protocol):
    """Resolution boundary shared by portfolio mutating workflows."""

    def resolve_project(self, selector: str | None = None) -> ProjectRef: ...

    def resolve_repository(self, selector: str, *, project: str | None = None) -> RepositoryRef: ...


class PortfolioTargetService(Protocol):
    """Portfolio-owned target and scope selection for application use-cases.

    The application coordinates audit operations, but it must not duplicate
    project/repository selection rules.  Implementations resolve selectors
    against one gateway snapshot and expand only the explicitly requested
    mutation scope.
    """

    def resolve_project(self, selector: str | None = None) -> ProjectRef: ...

    def resolve_repository(self, selector: str, *, project: str | None = None) -> RepositoryRef: ...

    def targets(self, repo: str | None = None, project: str | None = None) -> tuple[RepositoryRef, ...]: ...

    def write_targets(
        self,
        repo: str | None,
        project: str | None,
        *,
        all_repos: bool = False,
        all_projects: bool = False,
        operation: str = "mutation",
    ) -> tuple[RepositoryRef, ...]: ...

    def linked_website_mapping(self, project_id: str) -> Mapping[str, tuple[str, ...]]: ...


@dataclass(frozen=True, slots=True)
class PortfolioAuditStatus:
    """Audit-owned status aggregate plus active-run identity for recon."""

    summary: AuditStatus
    active_runs: tuple[AuditRun, ...] = ()

    @classmethod
    def from_audit_status(cls, status: AuditStatus, *, active_runs: tuple[AuditRun, ...] = ()) -> PortfolioAuditStatus:
        return cls(summary=status, active_runs=active_runs)


class AuditStatusReader(Protocol):
    def status(self, repo_id: str) -> PortfolioAuditStatus: ...


ReconState = Literal[AuditRunState, "unchanged"]
"""Audit's own run states plus the one Portfolio decides before asking.

The vocabularies are deliberately unified rather than mapped.  ``started`` and
``already_running`` already mean the same thing on both sides, and the states
only Audit can reach -- ``queued``, ``up_to_date``, ``failed`` -- have no
Portfolio equivalent worth inventing: translating them would either lose the
distinction or invent one.  ``unchanged`` is the single state Portfolio owns,
because the ``recon_done`` short-circuit happens before Audit is consulted.

It is declared here, at the one module allowed to see Audit's ports, so the
recon use-case never has to reach across the boundary itself.
"""


class AuditStartPort(Protocol):
    def start(self, repo_id: str, project_id: str, action_key: str) -> AuditStartOutcome: ...
