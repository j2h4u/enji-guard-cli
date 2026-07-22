"""High-level Audit orchestration over application ports."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from enji_guard_cli.audit.artifacts import (
    ArtifactReadItem,
    AuditArtifactUnavailableError,
    choose_report_ref,
    newer_run_for_report,
    select_artifacts,
)
from enji_guard_cli.audit.catalog import parse_catalog_result
from enji_guard_cli.audit.errors import AuditNotFoundError
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.observation import AuditRepositoryObservation
from enji_guard_cli.audit.ports import (
    AuditCatalogPort,
    AuditGatewayPort,
    AuditProject,
    AuditStatus,
    AuditStatusItem,
)
from enji_guard_cli.audit.status import build_status
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.settings import default_settings


@dataclass(frozen=True, slots=True)
class AuditWorkflowDependencies:
    catalog: AuditCatalogPort
    gateway: AuditGatewayPort
    project: Callable[[str], AuditProject]
    frozen_catalog: AuditCatalog | None = None
    repository_observation: Callable[[str], AuditRepositoryObservation] | None = None
    fanout: BoundedFanout | None = None


@dataclass(frozen=True, slots=True)
class AuditStartPlan:
    catalog: AuditCatalog
    status: AuditStatus
    selected: tuple[AuditDefinition, ...]


def choose_audits(catalog: AuditCatalog, selectors: list[str], *, all_audits: bool) -> tuple[AuditDefinition, ...]:
    if all_audits:
        if selectors:
            raise ValueError("pass audit selectors or --all, not both")
        return catalog.published_audits
    if not selectors:
        raise ValueError("pass at least one audit selector or --all")
    by_selector = {audit.selector: audit for audit in catalog.published_audits}
    return tuple(_required_audit(by_selector, selector) for selector in selectors)


def _required_audit(by_selector: dict[str, AuditDefinition], selector: str) -> AuditDefinition:
    audit = by_selector.get(selector)
    if audit is None:
        raise ValueError(f"unknown audit selector: {selector}")
    return audit


def prepare_start(
    repo_id: str,
    selectors: list[str],
    *,
    all_audits: bool,
    dependencies: AuditWorkflowDependencies,
) -> AuditStartPlan:
    catalog = _catalog(dependencies)
    observation = _observation(repo_id, dependencies)
    status = build_status(
        repo_id,
        catalog,
        observation.task_links,
        observation.active_runs,
        observation.rerun_state,
    )
    return AuditStartPlan(
        catalog=catalog,
        status=status,
        selected=choose_audits(catalog, selectors, all_audits=all_audits),
    )


def read_for_repo(
    repo_id: str,
    selectors: list[str],
    *,
    all_audits: bool,
    dependencies: AuditWorkflowDependencies,
    tolerate_unavailable: bool | None = None,
) -> tuple[ArtifactReadItem, ...]:
    catalog = _catalog(dependencies)
    observation = _observation(repo_id, dependencies)
    status = build_status(
        repo_id,
        catalog,
        observation.task_links,
        observation.active_runs,
        observation.rerun_state,
    )
    selected = select_artifacts(status.items, selectors, all_artifacts=all_audits, catalog=catalog)
    definitions = {audit.action_key: audit for audit in catalog.published_audits}
    tolerate = (not selectors) if tolerate_unavailable is None else tolerate_unavailable

    def read_item(item: AuditStatusItem) -> ArtifactReadItem:
        return _read_history_item(
            repo_id,
            item,
            definitions.get(item.audit_key),
            observation,
            dependencies.gateway,
            tolerate_unavailable=tolerate,
        )

    fanout = dependencies.fanout or BoundedFanout(default_settings().fanout)
    results = fanout.map(selected, read_item)
    if not selectors and not all_audits and tolerate_unavailable is None:
        return tuple(item for item in results if item.available)
    return results


def _read_history_item(  # noqa: PLR0913
    repo_id: str,
    item: AuditStatusItem,
    audit: AuditDefinition | None,
    observation: AuditRepositoryObservation,
    gateway: AuditGatewayPort,
    *,
    tolerate_unavailable: bool,
) -> ArtifactReadItem:
    if audit is None:
        return _unavailable(item, "status not found", tolerate_unavailable)
    metric_group = audit.metric_group or audit.action_key
    try:
        refs = gateway.list_audit_reports(repo_id, metric_group)
    except AuditNotFoundError:
        return _unavailable(item, "artifact_not_found", tolerate_unavailable)
    ref = choose_report_ref(refs)
    if ref is None or ref.task_id is None:
        return _unavailable(item, "artifact_not_found", tolerate_unavailable)
    try:
        artifact = gateway.read_audit_snapshot(
            repo_id,
            item.audit_key,
            metric_group,
            task_id=ref.task_id,
        )
    except AuditNotFoundError:
        return _unavailable(item, "artifact_not_found", tolerate_unavailable)
    artifact = replace(
        artifact,
        task_id=ref.task_id,
        completed_at=ref.completed_at,
        collected_at=ref.collected_at,
    )
    newer_run = newer_run_for_report(
        ref,
        observation.active_runs,
        action_key=item.audit_key,
        report_is_stale=item.freshness.state == "stale",
    )
    return ArtifactReadItem(item.audit_key, True, artifact, None, item.freshness, newer_run)


def _unavailable(item: AuditStatusItem, reason: str, tolerate: bool) -> ArtifactReadItem:
    if tolerate:
        return ArtifactReadItem(item.audit_key, False, None, reason, item.freshness)
    raise AuditArtifactUnavailableError(item.audit_key, reason)


def _catalog(dependencies: AuditWorkflowDependencies) -> AuditCatalog:
    """Fetch the account catalog exactly once for this top-level operation."""

    if dependencies.frozen_catalog is not None:
        return dependencies.frozen_catalog

    return parse_catalog_result(dependencies.catalog.catalog())


def _observation(repo_id: str, dependencies: AuditWorkflowDependencies) -> AuditRepositoryObservation:
    if dependencies.repository_observation is not None:
        return dependencies.repository_observation(repo_id)
    return AuditRepositoryObservation(
        task_links=dependencies.gateway.task_links(repo_id).links,
        active_runs=dependencies.gateway.active_runs(repo_id).runs,
        rerun_state=dependencies.gateway.rerun_state(repo_id),
    )
