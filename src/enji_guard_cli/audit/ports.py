"""Application ports and DTOs owned by the Audit bounded context."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from enji_guard_cli.audit.errors import AuditMalformedError
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
    repository_provider: str
    repository_locator: str
    repository_web_url: str | None = None


@dataclass(frozen=True, slots=True)
class AuditRepository:
    repo_id: str
    provider: str
    locator: str
    connected: bool
    web_url: str | None = None


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
    runbook_id: str | None = None
    artifact_schema_name: str | None = None
    artifact_schema_version: str | None = None
    task_description_template: str | None = None


@dataclass(frozen=True, slots=True)
class AuditCatalogAutofix:
    action_key: str
    variant_key: str
    title: str | None = None
    description: str | None = None
    runbook_id: str | None = None
    status: str | None = None
    sort_order: int | None = None


@dataclass(frozen=True, slots=True)
class AuditCatalogChange:
    """Typed catalog change observation; no upstream payload crosses this port."""

    kind: Literal["added", "removed", "changed"]
    action_key: str
    changed_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuditCatalogResult:
    """Published Audit actions available from the account catalog."""

    actions: tuple[AuditCatalogAction, ...]
    autofixes: tuple[AuditCatalogAutofix, ...] = ()
    changes: tuple[AuditCatalogChange, ...] = ()


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
class AuditItemStatus:
    """Neutral audit/task status used by Audit run orchestration."""

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


AuditFreshnessState = Literal["fresh", "stale", "unknown"]
AuditTaskLifecycle = Literal["none", "queued", "running", "failed", "completed"]


@dataclass(frozen=True, slots=True)
class AuditFreshness:
    """Explicit applicability of a completed artifact to the current source."""

    current_head_sha: str | None
    audited_head_sha: str | None
    state: AuditFreshnessState

    @property
    def stale(self) -> bool | None:
        return {"fresh": False, "stale": True, "unknown": None}[self.state]


@dataclass(frozen=True, slots=True)
class AuditStatusItem:
    """Status of one published audit, independent of upstream wire fields."""

    audit_key: str
    title: str
    freshness: AuditFreshness
    can_read: bool
    task_lifecycle: AuditTaskLifecycle
    task_id: str | None
    task_status: str | None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    @property
    def active(self) -> bool:
        return self.task_lifecycle in {"queued", "running"}


@dataclass(frozen=True, slots=True)
class AuditStatus:
    """Repository-wide audit status with mixed and partial state visible."""

    repo_id: str
    current_head_sha: str | None
    items: tuple[AuditStatusItem, ...]

    @property
    def readable(self) -> tuple[str, ...]:
        return tuple(item.audit_key for item in self.items if item.can_read)

    @property
    def active(self) -> tuple[str, ...]:
        return tuple(item.audit_key for item in self.items if item.active)

    @property
    def stale(self) -> tuple[str, ...]:
        return tuple(item.audit_key for item in self.items if item.freshness.state == "stale")

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(item.audit_key for item in self.items if not item.can_read)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(item.audit_key for item in self.items if item.task_lifecycle == "failed")

    @property
    def complete(self) -> bool:
        return not self.active and not self.missing and not self.failed

    @property
    def fresh(self) -> bool:
        return not self.stale

    @property
    def partial(self) -> bool:
        return bool(self.readable) and bool(self.missing)

    @property
    def mixed(self) -> bool:
        states = {item.freshness.state for item in self.items}
        return len(states) > 1


@dataclass(frozen=True, slots=True)
class AuditWaitOptions:
    poll_seconds: float
    timeout_seconds: float
    heartbeat_seconds: float


@dataclass(frozen=True, slots=True)
class AuditWaitResult:
    repo_id: str
    status: AuditStatus
    complete: bool
    timed_out: bool
    reason: Literal["complete", "waiting", "failed", "missing", "stale", "timeout"]
    elapsed_seconds: int


@dataclass(frozen=True, slots=True)
class AuditSchedule:
    audit_key: str
    enabled: bool
    cadence: str | None
    schedule_day: str | None
    schedule_day_of_month: int | None
    schedule_time: str | None
    schedule_time_source: Literal["auto", "user"] | None
    timezone: str | None
    window_days: tuple[str, ...] = ()
    window_start_time: str | None = None
    window_end_time: str | None = None
    window_mode: str | None = None


@dataclass(frozen=True, slots=True)
class AuditScheduleUpdate:
    enabled: bool | None = None
    cadence: str | None = None
    window_days: tuple[str, ...] | None = None
    schedule_time: str | None = None
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class AuditAutofixDefinition:
    action_key: str
    variant_key: str
    title: str | None
    description: str | None
    source_audit: str | None
    kind: str | None
    supported: bool
    runbook_id: str | None = None
    sort_order: int | None = None

    @property
    def selector(self) -> str:
        return self.kind or self.action_key.removeprefix("improvement.")


@dataclass(frozen=True, slots=True)
class AuditAutofixUpdate:
    enabled: bool | None
    frequency: str | None = None
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class AuditAutofixJob:
    """Typed improvement job crossing the Audit gateway boundary.

    ``extensions`` preserves provider fields without exposing a JSON object to
    Audit workflows.  Known fields remain product-language values.
    """

    action_key: str
    variant_key: str
    kind: str | None = None
    enabled: bool | None = None
    auto_fix: bool | None = None
    autofix_variant_key: str | None = None
    frequency: str | None = None
    days_of_week: tuple[str, ...] = ()
    schedule_time: str | None = None
    schedule_time_source: Literal["auto", "user"] | None = None
    timezone: str | None = None
    pentest_mode: str | None = None
    extensions: tuple[tuple[str, JsonValue], ...] = ()


@dataclass(frozen=True, slots=True)
class AuditEmailPreference:
    """Completion-email choices for one repository audit."""

    audit_key: str
    manual: bool | None = None
    scheduled: bool | None = None


@dataclass(frozen=True, slots=True)
class AuditEmailPreferenceUpdate:
    manual: bool | None = None
    scheduled: bool | None = None


@dataclass(frozen=True, slots=True)
class AuditLedgerEntry:
    repo_id: str
    project_id: str
    audit_key: str
    task_id: str | None
    task_status: str | None
    current_head_sha: str | None
    audited_head_sha: str | None
    observed_at: datetime
    started_at: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuditLedgerProjection:
    entries: tuple[AuditLedgerEntry, ...]


class AuditCatalogPort(Protocol):
    """Application-facing catalog access; implementations fetch it once per operation."""

    def catalog(self) -> AuditCatalogResult: ...


class AuditLedgerPort(Protocol):
    def record_started(self, entry: AuditLedgerEntry) -> None: ...

    def active_for(
        self, repo_id: str, audit_key: str | None = None, *, now: datetime | None = None
    ) -> tuple[AuditLedgerEntry, ...]: ...

    def reconcile(
        self,
        repo_id: str,
        upstream: Sequence[AuditRun],
        task_lookup: Callable[[str], AuditTaskDetail],
        *,
        now: datetime | None = None,
    ) -> tuple[AuditRun, ...]: ...

    def prune(
        self,
        *,
        now: datetime | None = None,
        current_head_sha: str | None = None,
        audited_head_shas: dict[str, str] | None = None,
    ) -> int: ...


class MalformedAuditSnapshotError(AuditMalformedError):
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

    def read_audit_snapshot(self, repo_id: str, audit_key: str, metric_group: str | None = None) -> AuditArtifact: ...

    def list_schedules(self, repo_id: str) -> tuple[AuditSchedule, ...]: ...

    def set_schedule(self, repo_id: str, audit_key: str, schedule: AuditSchedule) -> AuditSchedule: ...

    def get_email_preferences(self, repo_id: str, audit_key: str) -> AuditEmailPreference: ...

    def set_email_preference(
        self, repo_id: str, audit_key: str, update: AuditEmailPreferenceUpdate
    ) -> AuditEmailPreference: ...

    def list_autofix_jobs(self, repo_id: str) -> tuple[AuditAutofixJob, ...]: ...

    def set_autofix_job(self, repo_id: str, kind: str, job: AuditAutofixJob) -> AuditAutofixJob: ...
