"""High-level Audit orchestration over application ports."""

from collections.abc import Callable
from dataclasses import dataclass

from enji_guard_cli.audit.artifacts import ArtifactReadItem, read_artifacts, select_artifacts
from enji_guard_cli.audit.catalog import parse_catalog_result
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import (
    AuditCatalogPort,
    AuditGatewayPort,
    AuditProject,
    AuditStatus,
)
from enji_guard_cli.audit.preflight import AuditPreflight, build_preflight, snapshots_to_preserve
from enji_guard_cli.audit.status import build_status


@dataclass(frozen=True, slots=True)
class AuditWorkflowDependencies:
    catalog: AuditCatalogPort
    gateway: AuditGatewayPort
    project: Callable[[str], AuditProject]
    frozen_catalog: AuditCatalog | None = None


@dataclass(frozen=True, slots=True)
class AuditStartPlan:
    catalog: AuditCatalog
    status: AuditStatus
    preflight: AuditPreflight
    snapshots_to_preserve: tuple[str, ...]
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
    status = build_status(
        repo_id,
        catalog,
        dependencies.gateway.task_links(repo_id).links,
        dependencies.gateway.active_runs(repo_id).runs,
        dependencies.gateway.rerun_state(repo_id),
    )
    return AuditStartPlan(
        catalog=catalog,
        status=status,
        preflight=build_preflight(status),
        snapshots_to_preserve=snapshots_to_preserve(status),
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
    status = build_status(
        repo_id,
        catalog,
        dependencies.gateway.task_links(repo_id).links,
        dependencies.gateway.active_runs(repo_id).runs,
        dependencies.gateway.rerun_state(repo_id),
    )
    selected = select_artifacts(status.items, selectors, all_artifacts=all_audits, catalog=catalog)
    groups = {audit.action_key: audit.metric_group for audit in catalog.published_audits}
    return read_artifacts(
        repo_id,
        selected,
        reader=lambda target, key: dependencies.gateway.read_audit_snapshot(target, key, groups.get(key)),
        tolerate_unavailable=(not selectors) if tolerate_unavailable is None else tolerate_unavailable,
    )


def _catalog(dependencies: AuditWorkflowDependencies) -> AuditCatalog:
    """Fetch the account catalog exactly once for this top-level operation."""

    if dependencies.frozen_catalog is not None:
        return dependencies.frozen_catalog

    return parse_catalog_result(dependencies.catalog.catalog())
