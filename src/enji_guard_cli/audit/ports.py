"""Application ports and DTOs owned by the Audit bounded context."""

from dataclasses import dataclass, field
from typing import Protocol

from enji_guard_cli.json_types import JsonValue

type AuditFlowConfig = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class AuditTaskBody:
    """Neutral task description assembled by the Audit application."""

    title: str
    description: str
    project_id: str
    execution_flow: str
    flow_config: AuditFlowConfig
    runbook_id: str
    scope_owner: str
    repository_full_name: str


@dataclass(frozen=True, slots=True)
class AuditRepository:
    repo_id: str
    full_name: str
    connected: bool


@dataclass(frozen=True, slots=True)
class AuditWebsite:
    url: str
    repo_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditProject:
    """Project information required to create an Audit task."""

    project_id: str
    repositories: tuple[AuditRepository, ...]
    linked_websites: tuple[AuditWebsite, ...]


@dataclass(frozen=True, slots=True)
class AuditRunRequest:
    """Product-language input for starting one audit run."""

    repo_id: str
    project_id: str
    action_key: str
    task_body: AuditTaskBody


@dataclass(frozen=True, slots=True)
class AuditCatalogAction:
    """The catalog fields used by Audit to select an action."""

    action_key: str
    title: str
    category: str | None
    status: str | None
    metric_group: str | None
    runbook_kind: str | None


@dataclass(frozen=True, slots=True)
class AuditCatalogResult:
    """Published Audit actions available from the account catalog."""

    actions: tuple[AuditCatalogAction, ...]


@dataclass(frozen=True, slots=True)
class AuditRun:
    """One audit run as projected by the upstream service."""

    task_id: str | None
    action_key: str | None
    status: str | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None
    projection_source: str | None = None
    projection_status_source: str | None = None
    expires_at: str | None = None
    current_head_sha: str | None = None
    last_audited_head_sha: str | None = None


@dataclass(frozen=True, slots=True)
class AuditReportStatus:
    """Neutral report/task status used by Audit run orchestration."""

    action_key: str
    current_head_sha: str | None
    audited_head_sha: str | None
    can_read: bool
    completed_at: str | None
    task_id: str | None
    task_status: str | None
    task_active: bool


@dataclass(frozen=True, slots=True)
class AuditRunsResult:
    """Runs currently projected for one repository."""

    runs: tuple[AuditRun, ...]


@dataclass(frozen=True, slots=True)
class AuditRerunState:
    """Repository SHA and rerun state needed by Audit."""

    current_head_sha: str | None
    audited_head_sha: str | None
    rerun_allowed: bool | None
    last_task_id: str | None
    audited_head_shas: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditTaskLink:
    """The Audit identity associated with an upstream task."""

    task_id: str | None
    action_key: str | None
    status: str | None
    artifact_schema_name: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


@dataclass(frozen=True, slots=True)
class AuditTaskLinksResult:
    """Task links projected for one repository."""

    links: tuple[AuditTaskLink, ...]


@dataclass(frozen=True, slots=True)
class AuditTaskDetail:
    """The task fields needed to reconcile one audit run."""

    task_id: str
    status: str | None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


@dataclass(frozen=True, slots=True)
class AuditRunResult:
    """The task identity returned after starting an audit run."""

    task_id: str | None
    status: str | None


@dataclass(frozen=True, slots=True)
class AuditRunbookMetadata:
    """Runbook metadata needed to assemble an audit task."""

    runbook_id: str
    title: str | None
    description: str | None
    suggested_flow: str | None = None
    suggested_flow_config: AuditFlowConfig = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditArtifact:
    """The product-owned result of reading one completed audit snapshot."""

    audit_key: str
    body: str
    score: int | float | None = None
    generated_at: str | None = None


class MalformedAuditSnapshotError(ValueError):
    """Raised when the upstream service returns an unusable audit snapshot."""


class AuditGatewayPort(Protocol):
    """Narrow upstream operations required by the Audit context."""

    def catalog(self) -> AuditCatalogResult: ...

    def active_runs(self, repo_id: str) -> AuditRunsResult: ...

    def rerun_state(self, repo_id: str) -> AuditRerunState: ...

    def task_links(self, repo_id: str) -> AuditTaskLinksResult: ...

    def task_detail(self, task_id: str) -> AuditTaskDetail: ...

    def runbook_metadata(self, runbook_id: str) -> AuditRunbookMetadata: ...

    def start_audit_run(self, request: AuditRunRequest) -> AuditRunResult: ...

    def read_audit_snapshot(self, repo_id: str, audit_key: str) -> AuditArtifact: ...
