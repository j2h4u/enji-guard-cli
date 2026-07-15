"""Translation of Enji wire payloads into Audit gateway results."""

from enji_guard_cli.enji_gateway.ports import (
    AuditArtifact,
    AuditRerunState,
    AuditRun,
    AuditTaskDetail,
    AuditTaskLink,
    MalformedAuditSnapshotError,
)
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue


def audit_run_from_legacy_payload(payload: dict[str, JsonValue]) -> AuditRun:
    task = payload.get("task")
    task_payload = task if isinstance(task, dict) else {}
    return AuditRun(
        task_id=_optional_str(payload.get("fleetTaskId")) or _optional_str(task_payload.get("id")),
        action_key=_optional_str(payload.get("actionKey")) or _optional_str(task_payload.get("actionKey")),
        status=_optional_str(payload.get("status")) or _optional_str(task_payload.get("status")),
        created_at=_optional_str(payload.get("createdAt")) or _optional_str(task_payload.get("createdAt")),
        started_at=_optional_str(payload.get("startedAt")) or _optional_str(task_payload.get("startedAt")),
        completed_at=_optional_str(payload.get("completedAt")) or _optional_str(task_payload.get("completedAt")),
        projection_source=_optional_str(payload.get("projectionSource")),
        projection_status_source=_optional_str(payload.get("projectionStatusSource")),
        expires_at=_optional_str(payload.get("expiresAt")),
        current_head_sha=_optional_str(payload.get("currentHeadSha")),
        last_audited_head_sha=_optional_str(payload.get("lastAuditedHeadSha")),
    )


def audit_runs_from_legacy_payload(payload: JsonObjectPayload) -> tuple[AuditRun, ...]:
    runs = payload.get("activeRuns")
    return (
        tuple(audit_run_from_legacy_payload(run) for run in runs if isinstance(run, dict))
        if isinstance(runs, list)
        else ()
    )


def audit_task_link_from_legacy_payload(payload: dict[str, JsonValue]) -> AuditTaskLink:
    return AuditTaskLink(
        task_id=_optional_str(payload.get("fleetTaskId")),
        action_key=_optional_str(payload.get("actionKey")),
        status=_optional_str(payload.get("status")),
        artifact_schema_name=_optional_str(payload.get("artifactSchemaName")),
        created_at=_optional_str(payload.get("createdAt")),
        started_at=_optional_str(payload.get("startedAt")),
        completed_at=_optional_str(payload.get("completedAt")),
    )


def audit_rerun_state_from_legacy_payload(payload: JsonObjectPayload) -> AuditRerunState:
    state = payload.get("state")
    state_payload = state if isinstance(state, dict) else {}
    actions = state_payload.get("actions")
    action_payload = actions if isinstance(actions, dict) else {}
    return AuditRerunState(
        current_head_sha=_optional_str(state_payload.get("currentHeadSha")),
        audited_head_sha=_optional_str(state_payload.get("lastAuditedSha")),
        rerun_allowed=_optional_bool(state_payload.get("canRerun")),
        last_task_id=_optional_str(state_payload.get("lastFleetTaskId")),
        audited_head_shas={
            action_key: audited_sha
            for action_key, action in action_payload.items()
            if isinstance(action_key, str)
            and isinstance(action, dict)
            and (audited_sha := _optional_str(action.get("lastAuditedHeadSha"))) is not None
        },
    )


def audit_task_links_from_legacy_payload(payload: JsonObjectPayload) -> tuple[AuditTaskLink, ...]:
    links = payload.get("links")
    return (
        tuple(audit_task_link_from_legacy_payload(link) for link in links if isinstance(link, dict))
        if isinstance(links, list)
        else ()
    )


def audit_task_detail_from_legacy_payload(payload: JsonObjectPayload, task_id: str) -> AuditTaskDetail:
    task = payload.get("task")
    task_payload = task if isinstance(task, dict) else payload
    return AuditTaskDetail(
        task_id=_optional_str(task_payload.get("id")) or task_id,
        status=_optional_str(task_payload.get("status")),
        created_at=_optional_str(task_payload.get("createdAt")),
        started_at=_optional_str(task_payload.get("startedAt")),
        completed_at=_optional_str(task_payload.get("completedAt")),
    )


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
    return AuditArtifact(
        audit_key=audit_key,
        body=body,
        score=score,
        generated_at=generated_at if isinstance(generated_at, str) else None,
    )


def _optional_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: JsonValue | None) -> bool | None:
    return value if isinstance(value, bool) else None
