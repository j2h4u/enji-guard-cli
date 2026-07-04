from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict

from enji_guard_cli.json_types import JsonObjectPayload, JsonValue
from enji_guard_cli.settings import (
    DEFAULT_REPO_SORT as SETTINGS_DEFAULT_REPO_SORT,
)
from enji_guard_cli.settings import (
    DEFAULT_REPORT_WAIT_HEARTBEAT_SECONDS as SETTINGS_DEFAULT_REPORT_WAIT_HEARTBEAT_SECONDS,
)
from enji_guard_cli.settings import (
    DEFAULT_REPORT_WAIT_POLL_SECONDS as SETTINGS_DEFAULT_REPORT_WAIT_POLL_SECONDS,
)
from enji_guard_cli.settings import (
    DEFAULT_REPORT_WAIT_TIMEOUT_SECONDS as SETTINGS_DEFAULT_REPORT_WAIT_TIMEOUT_SECONDS,
)

type OperationResult = object | Awaitable[object]
type OperationExecutor = Callable[..., OperationResult]
type RepoSort = Literal["default", "name", "weakest", "overall", "latest-report"]
type ScheduleFrequency = Literal["daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"]

OWNER_NAME_SLUG_PARTS = 2
REPORT_ARTIFACT_SCHEMA = "upfront.audit.summary"
RECON_REPORT_SCHEMA = "upfront.recon.report"
AUDIT_REPORT_SCHEMA = "upfront.audit.report"
DEFAULT_EXECUTION_FLOW = "single"
DEFAULT_FLOW_CONFIG: JsonObjectPayload = {}
DEFAULT_REPO_SORT: RepoSort = SETTINGS_DEFAULT_REPO_SORT
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "canceled", "cancelled", "skipped"})
WORKDAY_SCHEDULE_DAYS = ("mon", "tue", "wed", "thu", "fri")
ALL_SCHEDULE_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_SCHEDULE_DAYS_BY_FREQUENCY: dict[ScheduleFrequency, tuple[str, ...]] = {
    "daily": ALL_SCHEDULE_DAYS,
    "workdays": WORKDAY_SCHEDULE_DAYS,
    "weekly-3x": ("mon", "wed", "fri"),
    "weekly-2x": ("mon", "thu"),
    "weekly": ("mon",),
    "monthly": ("mon",),
}
SCHEDULE_TIME_PARTS = 2
MAX_SCHEDULE_HOUR = 23
MAX_SCHEDULE_MINUTE = 59
SCORE_POOR_THRESHOLD = 40.0
SCORE_FAIR_THRESHOLD = 60.0
SCORE_GOOD_THRESHOLD = 75.0
SCORE_EXCELLENT_THRESHOLD = 90.0


class AuditRunBatchResultItem(TypedDict):
    audit: str
    action_key: str
    state: Literal["started", "queued", "already_running", "up_to_date", "failed"]
    current_head_sha: str | None
    last_audited_head_sha: str | None
    task_id: NotRequired[str | None]
    task_status: NotRequired[str | None]


class AuditRunBatchPayload(TypedDict):
    results: list[AuditRunBatchResultItem]


class AuditRunSkippedPayload(TypedDict):
    skipped: bool
    audit: str
    action_key: str
    reason: str
    active_runs: list[JsonValue]


type ScoreGrade = Literal["critical", "poor", "fair", "good", "excellent"]


class ScoreSummaryPayload(TypedDict):
    overall_score: float | None
    overall_grade: ScoreGrade | None
    weakest_axis: str | None
    weakest_score: float | None
    weakest_grade: ScoreGrade | None


class RepoTargetPayload(TypedDict):
    project_id: str
    project_name: str | None
    repo_id: str
    github_owner: str | None
    github_name: str | None
    github_repo: str | None
    connected: bool | None
    recon_done: bool | None
    scores: JsonObjectPayload
    score_grades: dict[str, ScoreGrade]
    score_summary: ScoreSummaryPayload


class RepoResolvePayload(TypedDict):
    selector: str
    resolved: bool
    matches: list[RepoTargetPayload]


class ProjectRef(TypedDict):
    id: str
    name: str | None


type ReportReadabilityState = Literal["readable", "unavailable"]
type ReportFreshnessState = Literal["fresh", "stale", "unknown"]
type ReportTaskLifecycleState = Literal["none", "queued", "running", "failed"]
type ReportReadState = Literal["missing", "ready", "running"]
type ReportWaitReason = Literal["complete", "waiting", "timeout", "failed", "stale", "missing"]
type ReportWaitCallback = Callable[[dict[str, object]], None]

DEFAULT_REPORT_WAIT_POLL_SECONDS = SETTINGS_DEFAULT_REPORT_WAIT_POLL_SECONDS
DEFAULT_REPORT_WAIT_TIMEOUT_SECONDS = SETTINGS_DEFAULT_REPORT_WAIT_TIMEOUT_SECONDS
DEFAULT_REPORT_WAIT_HEARTBEAT_SECONDS = SETTINGS_DEFAULT_REPORT_WAIT_HEARTBEAT_SECONDS
FAILED_REPORT_WAIT_STATUSES = frozenset({"failed", "canceled", "cancelled"})


class ReportArtifactStatusPayload(TypedDict):
    readability_state: ReportReadabilityState
    can_read: bool
    freshness_state: ReportFreshnessState
    current_head_sha: str | None
    audited_head_sha: str | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None
    run_status: str | None
    fleet_task_id: str | None
    stale: bool | None


class ReportTaskStatusPayload(TypedDict):
    lifecycle_state: ReportTaskLifecycleState
    active: bool
    fleet_task_id: str | None
    run_status: str | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None


class ReportAuditStatusPayload(TypedDict):
    audit: str
    label: str
    action_key: str
    route_slug: str
    report: ReportArtifactStatusPayload
    task: ReportTaskStatusPayload
    agent_action: str | None


class ReportStatusPayload(TypedDict):
    schema_version: int
    repo_id: str
    current_head_sha: str | None
    last_report_at: str | None
    complete: bool
    fresh: bool
    readable: bool
    active: bool
    queued: bool
    running: bool
    missing: bool
    stale: bool
    failed: bool
    counts: ReportWaitCountsPayload
    items: list[ReportAuditStatusPayload]


class ReportWaitCountsPayload(TypedDict):
    total: int
    readable: int
    active: int
    queued: int
    running: int
    missing: int
    stale: int
    failed: int


class ReportWaitPayload(TypedDict):
    repo_id: str
    complete: bool
    fresh: bool
    timed_out: bool
    reason: ReportWaitReason
    elapsed_seconds: int
    current_head_sha: str | None
    last_report_at: str | None
    counts: ReportWaitCountsPayload
    readable: list[str]
    active: list[str]
    queued: list[str]
    running: list[str]
    missing: list[str]
    stale: list[str]
    failed: list[str]
    items: list[ReportAuditStatusPayload]


class ReportReadItemPayload(TypedDict):
    audit: str
    current_head_sha: str | None
    last_audited_head_sha: str | None
    out_of_date: bool | None
    available: bool
    state: ReportReadState
    reason: str | None
    message: str | None
    error_code: NotRequired[str]
    snapshot: NotRequired[JsonObjectPayload]


class ReportReadPayload(TypedDict):
    reports: list[ReportReadItemPayload]


class RepoRuntimeStatusPayload(TypedDict):
    project_id: str
    project_name: str | None
    repo_id: str
    github_owner: str | None
    github_name: str | None
    github_repo: str | None
    connected: bool | None
    recon_done: bool | None
    scores: JsonObjectPayload
    score_grades: dict[str, ScoreGrade]
    score_summary: ScoreSummaryPayload
    active_run_count: int
    active_runs: list[JsonValue]
    current_head_sha: str | None
    last_report_at: str | None
    reports: ReportStatusPayload


class ProjectRuntimeStatusPayload(TypedDict):
    project_id: str
    project_name: str | None
    repos: list[RepoRuntimeStatusPayload]


class RepoStatusSummaryPayload(TypedDict):
    project_count: int
    repo_count: int
    connected_repo_count: int
    active_run_count: int
    recon_done_count: int
    report_complete_count: int


class RepoStatusAllPayload(TypedDict):
    observed_at: str
    summary: RepoStatusSummaryPayload
    projects: list[ProjectRuntimeStatusPayload]


class OperationName(StrEnum):
    CATALOG_AUDITS = "catalog_audits"
    CATALOG_AUDIT = "catalog_audit"
    ACCESS = "access"
    REPORTS_LIST = "reports_list"
    AUTH_STATUS = "auth_status"


class OperationPayload(TypedDict):
    name: str
    summary: str


@dataclass(frozen=True, slots=True)
class OperationSpec:
    name: OperationName
    summary: str
    execute: OperationExecutor


@dataclass(frozen=True, slots=True)
class ReportWaitOptions:
    poll_seconds: int
    timeout_seconds: int
    heartbeat_seconds: int


@dataclass(frozen=True, slots=True)
class ScheduleUpdate:
    enabled: bool
    auto_fix: bool
    frequency: ScheduleFrequency
    days_of_week: list[str]
    schedule_time: str
    timezone: str


@dataclass(frozen=True, slots=True)
class ScheduleSettingsUpdate:
    enabled: bool | None
    frequency: ScheduleFrequency | None
    days_of_week: list[str] | None
    schedule_time: str | None
    timezone: str | None


@dataclass(frozen=True, slots=True)
class EmailPreferenceUpdate:
    manual_run_completion: bool | None
    scheduled_run_completion: bool | None
