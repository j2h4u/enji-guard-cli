"""Translation of Enji wire payloads into Audit gateway results."""

from enji_guard_cli.audit.ports import (
    AuditArtifact,
    MalformedAuditSnapshotError,
)
from enji_guard_cli.json_types import JsonObjectPayload


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
