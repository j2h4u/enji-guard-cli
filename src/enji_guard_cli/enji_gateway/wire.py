"""Translation of Enji wire payloads into Audit gateway results."""

from datetime import datetime

from enji_guard_cli.audit.ports import (
    AuditArtifact,
    AuditReportRef,
    MalformedAuditSnapshotError,
)
from enji_guard_cli.json_types import JsonObjectPayload


def audit_report_refs_from_payload(
    payload: JsonObjectPayload,
    *,
    expected_repo_id: str | None = None,
    expected_metric_group: str | None = None,
) -> tuple[AuditReportRef, ...]:
    """Normalize the report-history response without leaking wire fields."""

    reports = payload.get("reports")
    if not isinstance(reports, list):
        raise MalformedAuditSnapshotError("audit reports payload is missing reports")
    result: list[AuditReportRef] = []
    seen_identity: tuple[str, str] | None = None
    for index, raw in enumerate(reports):
        if not isinstance(raw, dict):
            raise MalformedAuditSnapshotError(f"audit report {index} is malformed")
        task_id = _optional_text(raw.get("fleetTaskId"), f"audit report {index} fleetTaskId")
        completed_at = _optional_datetime(raw.get("completedAt"), f"audit report {index} completedAt")
        collected_at = _optional_text(raw.get("collectedAt"), f"audit report {index} collectedAt")
        has_report = raw.get("hasReport")
        if not isinstance(has_report, bool):
            raise MalformedAuditSnapshotError(f"audit report {index} hasReport must be boolean")
        repo_id = _optional_text(raw.get("repoId", payload.get("repoId")), f"audit report {index} repoId")
        metric_group = _optional_text(raw.get("group", payload.get("group")), f"audit report {index} group")
        if expected_repo_id is not None and repo_id != expected_repo_id:
            raise MalformedAuditSnapshotError(f"audit report {index} repoId does not match requested repository")
        if expected_metric_group is not None and metric_group != expected_metric_group:
            raise MalformedAuditSnapshotError(f"audit report {index} group does not match requested group")
        if repo_id is not None and metric_group is not None:
            identity = (repo_id, metric_group)
            if seen_identity is not None and identity != seen_identity:
                raise MalformedAuditSnapshotError("audit reports contain mixed repository or metric group")
            seen_identity = identity
        result.append(AuditReportRef(task_id, completed_at, collected_at, has_report))
    return tuple(result)


def audit_artifact_from_snapshot(snapshot: JsonObjectPayload, audit_key: str) -> AuditArtifact:
    """Translate ``snapshot.content.report`` exactly once at the wire boundary."""

    snapshot_value = snapshot.get("snapshot")
    if not isinstance(snapshot_value, dict):
        raise MalformedAuditSnapshotError(f"{audit_key} snapshot is missing snapshot content")
    content = snapshot_value.get("content")
    if not isinstance(content, dict):
        raise MalformedAuditSnapshotError(f"{audit_key} snapshot is missing content")
    body = content.get("report")
    if not isinstance(body, str):
        raise MalformedAuditSnapshotError(f"{audit_key} snapshot content is missing a text body")
    score = content.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = None
    generated_at = content.get("generatedAt")
    task_id = _optional_text(snapshot_value.get("fleetTaskId"), f"{audit_key} fleetTaskId")
    completed_at = _optional_datetime(snapshot_value.get("completedAt"), f"{audit_key} completedAt")
    collected_at = _optional_text(snapshot_value.get("collectedAt"), f"{audit_key} collectedAt")
    return AuditArtifact(
        audit_key=audit_key,
        body=body,
        score=score,
        generated_at=generated_at if isinstance(generated_at, str) else None,
        task_id=task_id,
        completed_at=completed_at,
        collected_at=collected_at,
    )


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedAuditSnapshotError(f"{field} must be string or null")
    return value


def _optional_datetime(value: object, field: str) -> str | None:
    """Validate optional upstream timestamps while preserving their wire text."""

    text = _optional_text(value, field)
    if text is None or not text.strip():
        return text
    try:
        datetime.fromisoformat(text.strip())
    except ValueError as exc:
        raise MalformedAuditSnapshotError(f"{field} must be valid ISO datetime or null") from exc
    return text
