"""Application ports for Portfolio and its narrow Audit dependency."""

from dataclasses import dataclass
from typing import Protocol

from enji_guard_cli.audit.ports import AuditItemStatus, AuditRun, AuditRunResult
from enji_guard_cli.portfolio.models import (
    AccessInfo,
    AccountPreferences,
    MovePreflight,
    ProjectDetail,
    ProjectRef,
    RepositoryRef,
)


class PortfolioGatewayPort(Protocol):
    """Typed project/repository operations supplied by an infrastructure adapter."""

    def list_projects(self) -> tuple[ProjectRef, ...]: ...
    def project_detail(self, project_id: str) -> ProjectDetail: ...
    def create_project(self, name: str) -> ProjectRef: ...
    def rename_project(self, project_id: str, name: str) -> ProjectRef: ...
    def delete_project(self, project_id: str) -> None: ...
    def add_repository(self, project_id: str, owner: str, name: str) -> RepositoryRef: ...
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


@dataclass(frozen=True, slots=True)
class PortfolioAuditStatus:
    """The small audit projection needed by Portfolio status and recon."""

    current_head_sha: str | None
    audited_head_shas: dict[str, str | None]
    audits: tuple[AuditItemStatus, ...] = ()
    active_runs: tuple[AuditRun, ...] = ()
    last_audit_at: str | None = None


class AuditStatusReader(Protocol):
    def status(self, repo_id: str) -> PortfolioAuditStatus: ...


class AuditStartPort(Protocol):
    def start(self, repo_id: str, project_id: str, action_key: str) -> AuditRunResult: ...


@dataclass(frozen=True, slots=True)
class PortfolioStatusPort:
    """Composition port used by CLI/MCP facades for status assembly."""

    gateway: PortfolioGatewayPort
    audits: AuditStatusReader
