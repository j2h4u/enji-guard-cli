"""Read completed audit artifacts without leaking gateway payload vocabulary."""

from collections.abc import Callable
from dataclasses import dataclass

from enji_guard_cli.audit.errors import AuditNotFoundError
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import AuditArtifact, AuditStatusItem


class AuditArtifactUnavailableError(AuditNotFoundError, ValueError):
    """Raised when a requested artifact cannot be read."""

    def __init__(self, audit_key: str, reason: str) -> None:
        self.audit_key = audit_key
        self.reason = reason
        super().__init__(f"{audit_key} artifact {reason}")


@dataclass(frozen=True, slots=True)
class ArtifactReadItem:
    audit_key: str
    available: bool
    artifact: AuditArtifact | None
    reason: str | None
    freshness: object


def select_artifacts(
    status: tuple[AuditStatusItem, ...],
    selectors: list[str],
    *,
    all_artifacts: bool,
    catalog: AuditCatalog,
) -> tuple[AuditStatusItem, ...]:
    """Resolve suffix selectors and enforce readability for explicit reads."""

    if all_artifacts and selectors:
        raise ValueError("pass audit selectors or --all, not both")
    if all_artifacts:
        return tuple(status)
    if not selectors:
        return tuple(item for item in status if item.can_read)
    by_selector = {audit.selector: audit.action_key for audit in catalog.published_audits}
    by_key = {item.audit_key: item for item in status}
    selected: list[AuditStatusItem] = []
    for selector in selectors:
        audit_key = by_selector.get(selector)
        item = by_key.get(audit_key) if audit_key else None
        if item is None:
            raise AuditArtifactUnavailableError(selector, "status not found")
        if not item.can_read:
            raise AuditArtifactUnavailableError(item.audit_key, _unreadable_reason(item))
        selected.append(item)
    return tuple(selected)


def read_artifacts(
    repo_id: str,
    items: tuple[AuditStatusItem, ...],
    *,
    reader: Callable[[str, str], AuditArtifact],
    tolerate_unavailable: bool,
) -> tuple[ArtifactReadItem, ...]:
    result: list[ArtifactReadItem] = []
    for item in items:
        if not item.can_read:
            if tolerate_unavailable:
                result.append(ArtifactReadItem(item.audit_key, False, None, _unreadable_reason(item), item.freshness))
                continue
            raise AuditArtifactUnavailableError(item.audit_key, _unreadable_reason(item))
        try:
            artifact = reader(repo_id, item.audit_key)
        except AuditArtifactUnavailableError:
            if not tolerate_unavailable:
                raise
            result.append(ArtifactReadItem(item.audit_key, False, None, "artifact_not_found", item.freshness))
            continue
        result.append(ArtifactReadItem(item.audit_key, True, artifact, None, item.freshness))
    return tuple(result)


def artifact_for_definition(
    repo_id: str,
    audit: AuditDefinition,
    *,
    reader: Callable[[str, str], AuditArtifact],
) -> AuditArtifact:
    if audit.metric_group is None:
        raise ValueError(f"{audit.action_key} is not a published audit")
    return reader(repo_id, audit.action_key)


def _unreadable_reason(item: AuditStatusItem) -> str:
    return {
        "queued": "queued",
        "running": "running",
        "failed": "failed",
    }.get(item.task_lifecycle, "missing")
