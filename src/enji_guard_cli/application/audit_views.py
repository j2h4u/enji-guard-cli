"""Application-owned presentation of audit results.

These are new types, mapped from :mod:`enji_guard_cli.audit` objects -- not
aliases, subclasses or re-exports of them.  Re-exporting the domain type from
``application/__init__`` would make the delivery import legal while leaving
the coupling exactly where it was; mapping is what actually moves the
operator vocabulary into this layer.

Field names deliberately match the domain objects they are built from,
because that is the operator-visible ``--json`` contract, not an accident of
the domain's internal naming.  The convenience properties are presentation
helpers only: ``dataclasses.asdict`` ignores them, so they never reach JSON.
"""

from dataclasses import dataclass
from typing import Literal

from enji_guard_cli.audit.artifacts import ArtifactReadItem, AuditRead, AuditSummary, AuditSummaryItem
from enji_guard_cli.audit.ports import (
    AuditArtifact,
    AuditCurrentHeadStatus,
    AuditFreshness,
    AuditNewerRun,
    AuditRun,
    AuditStatus,
    AuditStatusItem,
    AuditWaitResult,
)


@dataclass(frozen=True, slots=True)
class AuditFreshnessView:
    """Whether a completed artifact still applies to the current source."""

    current_head_sha: str | None
    audited_head_sha: str | None
    state: str


@dataclass(frozen=True, slots=True)
class AuditCurrentHeadView:
    """Readiness of one audit for the repository's current head."""

    state: str
    action_required: str
    task_id: str | None = None
    task_status: str | None = None
    task_current_head_sha: str | None = None
    stale_active_task_id: str | None = None
    stale_active_current_head_sha: str | None = None


@dataclass(frozen=True, slots=True)
class AuditNewerRunView:
    """An active task newer than the report being presented."""

    task_id: str
    status: str | None
    created_at: str | None
    started_at: str | None
    state: str


@dataclass(frozen=True, slots=True)
class AuditStatusItemView:
    """Status of one published audit."""

    audit_key: str
    title: str
    freshness: AuditFreshnessView
    can_read: bool
    task_lifecycle: str
    task_id: str | None
    task_status: str | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None
    current_head: AuditCurrentHeadView

    @property
    def active(self) -> bool:
        return self.task_lifecycle in {"queued", "running"}


@dataclass(frozen=True, slots=True)
class AuditStatusView:
    """Repository-wide audit status, with mixed and partial state visible."""

    repo_id: str
    current_head_sha: str | None
    items: tuple[AuditStatusItemView, ...]

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
    def failed(self) -> tuple[str, ...]:
        return tuple(item.audit_key for item in self.items if item.task_lifecycle == "failed")


@dataclass(frozen=True, slots=True)
class AuditRunView:
    """One audit run as the operator sees it."""

    task_id: str | None
    action_key: str | None
    status: str | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None
    projection_source: str | None
    projection_status_source: str | None
    expires_at: str | None
    current_head_sha: str | None
    last_audited_head_sha: str | None


@dataclass(frozen=True, slots=True)
class AuditArtifactView:
    """One completed audit report, body included."""

    audit_key: str
    body: str
    score: int | float | None
    generated_at: str | None
    task_id: str | None
    completed_at: str | None
    collected_at: str | None


@dataclass(frozen=True, slots=True)
class AuditReadItemView:
    """One audit's report, or the reason there is not one."""

    audit_key: str
    available: bool
    artifact: AuditArtifactView | None
    reason: str | None
    freshness: AuditFreshnessView
    newer_run: AuditNewerRunView | None


@dataclass(frozen=True, slots=True)
class AuditReadView:
    """Everything ``guard audit read`` renders."""

    repo_id: str
    audits: tuple[AuditReadItemView, ...]


@dataclass(frozen=True, slots=True)
class AuditSummaryItemView:
    """Compact audit metadata; the report body is deliberately absent."""

    audit_key: str
    available: bool
    score: int | float | None
    generated_at: str | None
    reason: str | None
    freshness: AuditFreshnessView
    task_id: str | None
    completed_at: str | None
    collected_at: str | None
    newer_run: AuditNewerRunView | None


@dataclass(frozen=True, slots=True)
class AuditSummaryView:
    """Everything ``guard audit summary`` renders."""

    repo_id: str
    audits: tuple[AuditSummaryItemView, ...]


@dataclass(frozen=True, slots=True)
class AuditWaitView:
    """Everything ``guard wait`` renders."""

    repo_id: str
    status: AuditStatusView
    complete: bool
    timed_out: bool
    reason: Literal["complete", "waiting", "failed", "missing", "timeout"]
    elapsed_seconds: int


def run_view(run: AuditRun) -> AuditRunView:
    return AuditRunView(
        task_id=run.task_id,
        action_key=run.action_key,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        projection_source=run.projection_source,
        projection_status_source=run.projection_status_source,
        expires_at=run.expires_at,
        current_head_sha=run.current_head_sha,
        last_audited_head_sha=run.last_audited_head_sha,
    )


def freshness_view(freshness: AuditFreshness) -> AuditFreshnessView:
    return AuditFreshnessView(
        current_head_sha=freshness.current_head_sha,
        audited_head_sha=freshness.audited_head_sha,
        state=freshness.state,
    )


def _current_head_view(head: AuditCurrentHeadStatus) -> AuditCurrentHeadView:
    return AuditCurrentHeadView(
        state=head.state,
        action_required=head.action_required,
        task_id=head.task_id,
        task_status=head.task_status,
        task_current_head_sha=head.task_current_head_sha,
        stale_active_task_id=head.stale_active_task_id,
        stale_active_current_head_sha=head.stale_active_current_head_sha,
    )


def _newer_run_view(run: AuditNewerRun | None) -> AuditNewerRunView | None:
    if run is None:
        return None
    return AuditNewerRunView(
        task_id=run.task_id,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        state=run.state,
    )


def _artifact_view(artifact: AuditArtifact | None) -> AuditArtifactView | None:
    if artifact is None:
        return None
    return AuditArtifactView(
        audit_key=artifact.audit_key,
        body=artifact.body,
        score=artifact.score,
        generated_at=artifact.generated_at,
        task_id=artifact.task_id,
        completed_at=artifact.completed_at,
        collected_at=artifact.collected_at,
    )


def _status_item_view(item: AuditStatusItem) -> AuditStatusItemView:
    return AuditStatusItemView(
        audit_key=item.audit_key,
        title=item.title,
        freshness=freshness_view(item.freshness),
        can_read=item.can_read,
        task_lifecycle=item.task_lifecycle,
        task_id=item.task_id,
        task_status=item.task_status,
        created_at=item.created_at,
        started_at=item.started_at,
        completed_at=item.completed_at,
        current_head=_current_head_view(item.current_head),
    )


def status_view(status: AuditStatus) -> AuditStatusView:
    return AuditStatusView(
        repo_id=status.repo_id,
        current_head_sha=status.current_head_sha,
        items=tuple(_status_item_view(item) for item in status.items),
    )


def _read_item_view(item: ArtifactReadItem) -> AuditReadItemView:
    return AuditReadItemView(
        audit_key=item.audit_key,
        available=item.available,
        artifact=_artifact_view(item.artifact),
        reason=item.reason,
        freshness=freshness_view(item.freshness),
        newer_run=_newer_run_view(item.newer_run),
    )


def read_view(read: AuditRead) -> AuditReadView:
    return AuditReadView(repo_id=read.repo_id, audits=tuple(_read_item_view(item) for item in read.audits))


def _summary_item_view(item: AuditSummaryItem) -> AuditSummaryItemView:
    return AuditSummaryItemView(
        audit_key=item.audit_key,
        available=item.available,
        score=item.score,
        generated_at=item.generated_at,
        reason=item.reason,
        freshness=freshness_view(item.freshness),
        task_id=item.task_id,
        completed_at=item.completed_at,
        collected_at=item.collected_at,
        newer_run=_newer_run_view(item.newer_run),
    )


def summary_view(summary: AuditSummary) -> AuditSummaryView:
    return AuditSummaryView(repo_id=summary.repo_id, audits=tuple(_summary_item_view(item) for item in summary.audits))


def wait_view(result: AuditWaitResult) -> AuditWaitView:
    return AuditWaitView(
        repo_id=result.repo_id,
        status=status_view(result.status),
        complete=result.complete,
        timed_out=result.timed_out,
        reason=result.reason,
        elapsed_seconds=result.elapsed_seconds,
    )


__all__ = [
    "AuditArtifactView",
    "AuditCurrentHeadView",
    "AuditFreshnessView",
    "AuditNewerRunView",
    "AuditReadItemView",
    "AuditReadView",
    "AuditRunView",
    "AuditStatusItemView",
    "AuditStatusView",
    "AuditSummaryItemView",
    "AuditSummaryView",
    "AuditWaitView",
    "freshness_view",
    "read_view",
    "run_view",
    "status_view",
    "summary_view",
    "wait_view",
]
