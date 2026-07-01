from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict

from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

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
DEFAULT_REPO_SORT: RepoSort = "default"
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


class AuditRunBatchItem(TypedDict):
    audit: str
    action_key: str
    response: JsonObjectPayload


class AuditRunSkippedItem(TypedDict):
    audit: str
    action_key: str
    reason: str
    active_runs: list[JsonValue]
    current_head_sha: str | None
    last_audited_head_sha: str | None


class AuditRunBatchPayload(TypedDict):
    runs: list[AuditRunBatchItem]
    skipped: list[AuditRunSkippedItem]


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


type ReportAuditState = Literal["missing", "ready", "running"]
type ReportWaitReason = Literal["complete", "waiting", "timeout", "failed", "stale"]
type ReportWaitCallback = Callable[[dict[str, object]], None]

DEFAULT_REPORT_WAIT_POLL_SECONDS = 30
DEFAULT_REPORT_WAIT_TIMEOUT_SECONDS = 2700
DEFAULT_REPORT_WAIT_HEARTBEAT_SECONDS = 120
FAILED_REPORT_WAIT_STATUSES = frozenset({"failed", "canceled", "cancelled"})


class ReportAuditStatusPayload(TypedDict):
    audit: str
    label: str
    action_key: str
    route_slug: str
    state: ReportAuditState
    ready: bool
    running: bool
    fleet_task_id: str | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None
    run_status: str | None
    current_head_sha: str | None
    last_audited_head_sha: str | None
    out_of_date: bool | None


class ReportStatusPayload(TypedDict):
    repo_id: str
    current_head_sha: str | None
    last_report_at: str | None
    complete: bool
    ready: list[str]
    running: list[str]
    missing: list[str]
    reports: list[ReportAuditStatusPayload]


class ReportWaitCountsPayload(TypedDict):
    total: int
    ready: int
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
    ready: list[str]
    running: list[str]
    missing: list[str]
    stale: list[str]
    failed: list[str]
    reports: list[ReportAuditStatusPayload]


class ReportReadItemPayload(TypedDict):
    audit: str
    current_head_sha: str | None
    last_audited_head_sha: str | None
    out_of_date: bool | None
    available: bool
    state: ReportAuditState
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
