"""Typed application composition for Audit and Portfolio use-cases.

Delivery code talks to this module (and to :class:`AuthSessionService`) only.
The gateway is the sole place that knows the upstream HTTP vocabulary; this
facade coordinates context use-cases and keeps selectors and write scope
explicit.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from enji_guard_cli.audit import parse_catalog_result
from enji_guard_cli.audit.autofixes import definitions as autofix_definitions
from enji_guard_cli.audit.autofixes import select as select_autofixes
from enji_guard_cli.audit.autofixes import set_one
from enji_guard_cli.audit.ledger import FileAuditLedger, new_entry
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import (
    AuditAutofixUpdate,
    AuditCatalogResult,
    AuditEmailPreferenceUpdate,
    AuditGatewayPort,
    AuditLedgerPort,
    AuditRunRequest,
    AuditRunResult,
    AuditSchedule,
    AuditScheduleUpdate,
    AuditStatus,
    AuditWaitOptions,
    AuditWaitResult,
)
from enji_guard_cli.audit.preflight import AuditPreflight, build_preflight
from enji_guard_cli.audit.runs import (
    RecordStartedRunContext,
    StartAuditDependencies,
    StartAuditsContext,
    start_audits_for_target,
)
from enji_guard_cli.audit.schedules import auto_time, plan_schedule_update
from enji_guard_cli.audit.status import audit_status_items, build_status
from enji_guard_cli.audit.wait import AuditWaitDependencies, wait_for_completion
from enji_guard_cli.audit.workflows import AuditWorkflowDependencies, choose_audits, read_for_repo
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.enji_gateway import AuditGateway, PortfolioGateway
from enji_guard_cli.json_types import JsonValue
from enji_guard_cli.portfolio.models import (
    AccessInfo,
    AccountPreferences,
    OperationResult,
    ProjectRef,
    ProjectSettings,
    RepositoryRef,
)
from enji_guard_cli.portfolio.ports import AuditStartPort, AuditStatusReader, PortfolioAuditStatus, PortfolioGatewayPort
from enji_guard_cli.portfolio.projects import create_project as create_project_use_case
from enji_guard_cli.portfolio.projects import delete_project as delete_project_use_case
from enji_guard_cli.portfolio.projects import rename_project as rename_project_use_case
from enji_guard_cli.portfolio.recon import recon_after_add
from enji_guard_cli.portfolio.recon import start_recon as start_recon_use_case
from enji_guard_cli.portfolio.repositories import add_repository, move_repository, remove_repository
from enji_guard_cli.portfolio.scopes import MutationScope
from enji_guard_cli.portfolio.selectors import GatewaySelectorResolver
from enji_guard_cli.portfolio.status import PortfolioStatus, assemble_status, status_for_repo
from enji_guard_cli.settings import RepositorySortName, default_settings


@dataclass(frozen=True, slots=True)
class EmailPreferencesUpdate:
    manual: bool | None = None
    scheduled: bool | None = None


@dataclass(frozen=True, slots=True)
class AutofixWriteScope:
    all_repos: bool = False
    all_projects: bool = False


@dataclass(slots=True)
class Application:
    """Composition root shared by CLI, MCP, and runtime supervisor."""

    audit_gateway: AuditGatewayPort
    portfolio_gateway: PortfolioGatewayPort
    auth: AuthSessionService
    ledger: AuditLedgerPort | None = None
    _last_catalog_result: AuditCatalogResult | None = None

    @classmethod
    def from_auth_file(cls, auth_file: Path | None = None) -> Application:
        settings = default_settings()
        ledger = FileAuditLedger(
            settings.active_run_ledger.state_file,
            ttl_seconds=settings.active_run_ledger.ttl_seconds,
            lookup_grace_seconds=settings.active_run_ledger.lookup_grace_seconds,
        )
        return cls(AuditGateway(auth_file), PortfolioGateway(auth_file), AuthSessionService(auth_file), ledger)

    # Catalog and Audit -------------------------------------------------
    def catalog(self) -> AuditCatalogResult:
        """Fetch the live catalog once; ``changes`` is the typed observation hook."""
        result = self.audit_gateway.catalog()
        self._last_catalog_result = result
        return result

    def catalog_observation(self) -> AuditCatalogResult | None:
        """Return the live result already fetched during this operation.

        Observation is read-only: a journey renderer must never turn its
        post-action hook into an extra catalog request.
        """
        return self._last_catalog_result

    def audit_catalog(self) -> AuditCatalog:
        return parse_catalog_result(self.catalog())

    def audit_status(self, repo_id: str, *, catalog: AuditCatalog | None = None) -> AuditStatus:
        definitions = catalog if catalog is not None else self.audit_catalog()
        return build_status(
            repo_id,
            definitions,
            self.audit_gateway.task_links(repo_id).links,
            self._active_runs(repo_id),
            self.audit_gateway.rerun_state(repo_id),
        )

    def audit_start(
        self, repo: str, project: str | None = None, selectors: list[str] | None = None, *, all_audits: bool = False
    ) -> object:
        target = self._resolve_repository(repo, project)
        catalog = self.audit_catalog()
        selected = self._select_audits(catalog, selectors or [], all_audits=all_audits)
        self._preserve_snapshots(target.repo_id, catalog)
        batch = self._start_batch(target.repo_id, target.project_id, selected, catalog)
        return {"repo_id": target.repo_id, "project_id": target.project_id, **batch}

    def audit_start_one(self, repo_id: str, project_id: str, audit: AuditDefinition) -> object:
        catalog = self.audit_catalog()
        self._preserve_snapshots(repo_id, catalog)
        batch = self._start_batch(repo_id, project_id, (audit,), catalog)
        return cast(list[dict[str, object]], batch["results"])[0]

    def audit_read(
        self, repo: str, selectors: list[str] | None = None, *, project: str | None = None, all_audits: bool = False
    ) -> object:
        target = self._resolve_repository(repo, project)
        catalog = self.audit_catalog()
        items = read_for_repo(
            target.repo_id,
            selectors or [],
            all_audits=all_audits,
            dependencies=AuditWorkflowDependencies(
                catalog=self.audit_gateway,
                gateway=self.audit_gateway,
                project=self._audit_project,
                frozen_catalog=catalog,
            ),
        )
        return {"repo_id": target.repo_id, "audits": items}

    def audit_preflight(self, repo: str, *, project: str | None = None) -> AuditPreflight:
        target = self._resolve_repository(repo, project)
        return build_preflight(self.audit_status(target.repo_id))

    def audit_summary(self, repo: str, selectors: list[str] | None = None, *, project: str | None = None) -> object:
        return self.audit_read(repo, selectors, project=project, all_audits=not bool(selectors))

    def audit_wait(
        self,
        repo: str,
        *,
        project: str | None = None,
        options: AuditWaitOptions | None = None,
        heartbeat: object | None = None,
    ) -> AuditWaitResult:
        target = self._resolve_repository(repo, project)
        settings = default_settings().audit_wait
        selected = options or AuditWaitOptions(
            settings.poll_seconds, settings.timeout_seconds, settings.heartbeat_seconds
        )
        callback = cast(Callable[[AuditWaitResult], None] | None, heartbeat if callable(heartbeat) else None)
        catalog = self.audit_catalog()
        return wait_for_completion(
            target.repo_id,
            options=selected,
            heartbeat=callback,
            dependencies=AuditWaitDependencies(
                lambda repo_id: self.audit_status(repo_id, catalog=catalog), time.monotonic, time.sleep
            ),
        )

    # Portfolio --------------------------------------------------------
    def list_projects(self) -> tuple[ProjectRef, ...]:
        return self.portfolio_gateway.list_projects()

    def create_project(self, name: str) -> OperationResult:
        return create_project_use_case(name, gateway=self.portfolio_gateway)

    def rename_project(self, project: str, name: str) -> OperationResult:
        return rename_project_use_case(project, name, gateway=self.portfolio_gateway)

    def delete_project(self, project: str) -> OperationResult:
        return delete_project_use_case(project, gateway=self.portfolio_gateway)

    def add_repository(self, repo: str, project: str | None = None) -> OperationResult:
        result = add_repository(repo, project, gateway=self.portfolio_gateway)
        if result.repository is not None:
            catalog = self.audit_catalog()
            recon = recon_after_add(
                result.repository, audits=_AuditStatusReader(self, catalog), starter=_AuditStarter(self, catalog)
            )
            return OperationResult(
                result.state,
                project=result.project,
                repository=result.repository,
                message=result.message,
                recon=recon,
            )
        return result

    def remove_repository(self, repo: str, project: str | None = None) -> OperationResult:
        return remove_repository(repo, project, gateway=self.portfolio_gateway)

    def move_repository(self, repo: str, source_project: str | None, target_project: str) -> OperationResult:
        return move_repository(repo, source_project, target_project, gateway=self.portfolio_gateway)

    def resolve_repository(self, repo: str, project: str | None = None) -> RepositoryRef:
        return self._resolve_repository(repo, project)

    def recon_start(self, repo: str, project: str | None = None) -> object:
        target = self._resolve_repository(repo, project)
        catalog = self.audit_catalog()
        return start_recon_use_case(
            target, audits=_AuditStatusReader(self, catalog), starter=_AuditStarter(self, catalog)
        )

    def portfolio_status(self, sort: RepositorySortName = "default") -> PortfolioStatus:
        catalog = self.audit_catalog()
        return assemble_status(gateway=self.portfolio_gateway, audits=_AuditStatusReader(self, catalog), sort=sort)

    def repository_status(self, repo: str, project: str | None = None) -> object:
        catalog = self.audit_catalog()
        return status_for_repo(repo, project, gateway=self.portfolio_gateway, audits=_AuditStatusReader(self, catalog))

    # Schedules, autofixes, preferences --------------------------------
    def list_schedules(self, repo: str | None = None, project: str | None = None) -> tuple[object, ...]:
        catalog = self.audit_catalog()
        return tuple(
            schedule_for_audit(audit, schedules)
            for target in self._targets(repo, project)
            for schedules in (self.audit_gateway.list_schedules(target.repo_id),)
            for audit in catalog.published_audits
        )

    def set_schedules(
        self,
        repo: str | None,
        project: str | None,
        update: AuditScheduleUpdate,
        *,
        scope: AutofixWriteScope | None = None,
    ) -> tuple[object, ...]:
        targets = self._write_targets(repo, project, scope)
        catalog = self.audit_catalog()
        result: list[object] = []
        for target in targets:
            result.extend(self._set_schedules_for_target(target, catalog, update))
        return tuple(result)

    def _set_schedules_for_target(
        self, target: RepositoryRef, catalog: AuditCatalog, update: AuditScheduleUpdate
    ) -> tuple[object, ...]:
        existing = {item.audit_key: item for item in self.audit_gateway.list_schedules(target.repo_id)}
        values: list[object] = []
        for audit in catalog.published_audits:
            desired = plan_schedule_update(existing.get(audit.action_key), audit.action_key, update)
            if desired is None:
                continue
            current = existing.get(audit.action_key)
            values.append(
                desired
                if current == desired
                else self.audit_gateway.set_schedule(target.repo_id, audit.action_key, desired)
            )
        return tuple(values)

    def schedule_auto_time(
        self, repo: str | None, project: str | None = None, *, scope: AutofixWriteScope | None = None
    ) -> tuple[object, ...]:
        targets = self._write_targets(repo, project, scope)
        catalog = self.audit_catalog()
        published = {audit.action_key for audit in catalog.published_audits}
        result: list[object] = []
        for target in targets:
            for current in self.audit_gateway.list_schedules(target.repo_id):
                if current.audit_key not in published:
                    continue
                desired = auto_time(current)
                result.append(
                    current
                    if desired == current
                    else self.audit_gateway.set_schedule(target.repo_id, current.audit_key, desired)
                )
        return tuple(result)

    def list_autofixes(self, repo: str | None = None, project: str | None = None) -> tuple[object, ...]:
        catalog = self.catalog()
        definitions = autofix_definitions(catalog)
        return tuple(
            (target, definitions, _normalize_autofix_jobs(self.audit_gateway.list_autofix_jobs(target.repo_id)))
            for target in self._targets(repo, project)
        )

    def set_autofixes(
        self,
        repo: str | None,
        project: str | None,
        selectors: list[str],
        update: AuditAutofixUpdate,
        *,
        scope: AutofixWriteScope | None = None,
    ) -> tuple[object, ...]:
        catalog = self.catalog()
        selected = select_autofixes(selectors, autofix_definitions(catalog))
        result: list[object] = []
        for target in self._write_targets(repo, project, scope):
            jobs = _index_autofix_jobs(self.audit_gateway.list_autofix_jobs(target.repo_id))
            for definition in selected:
                existing = cast(
                    dict[str, object] | None,
                    jobs.get(definition.action_key) or jobs.get(definition.kind or definition.selector),
                )
                outcome = set_one(
                    definition,
                    existing,
                    update,
                    lambda kind, job, repo_id=target.repo_id: self.audit_gateway.set_autofix_job(
                        repo_id, kind, cast(dict[str, JsonValue], job)
                    ),
                )
                result.append(outcome)
        return tuple(result)

    def list_email_preferences(self, repo: str | None = None, project: str | None = None) -> tuple[object, ...]:
        catalog = self.audit_catalog()
        keys = tuple(audit.action_key for audit in catalog.published_audits)
        return tuple(
            (target, self.audit_gateway.list_email_preferences(target.repo_id, keys))
            for target in self._targets(repo, project)
        )

    def set_email_preferences(
        self,
        repo: str | None,
        project: str | None,
        update: EmailPreferencesUpdate,
        *,
        scope: AutofixWriteScope | None = None,
    ) -> tuple[object, ...]:
        typed = _email_preference_update(update)
        keys = tuple(a.action_key for a in self.audit_catalog().published_audits)
        return tuple(
            self.audit_gateway.set_email_preference(target.repo_id, key, typed)
            for target in self._write_targets(repo, project, scope)
            for key in keys
        )

    def language(self) -> AccountPreferences:
        return self.portfolio_gateway.get_preferences()

    def get_language(self) -> AccountPreferences:
        """Return account language for the delivery adapter."""
        return self.language()

    def set_language(self, language: str) -> AccountPreferences:
        return self.portfolio_gateway.set_preferences(AccountPreferences(language))

    def project_settings(self, project: str | None = None) -> ProjectSettings:
        """Return project membership plus account preferences exactly once."""
        selected = GatewaySelectorResolver(self.portfolio_gateway).resolve_project(project)
        detail = self.portfolio_gateway.project_detail(selected.project_id)
        return ProjectSettings(
            project=detail.project,
            repositories=detail.repositories,
            account_preferences=self.portfolio_gateway.get_preferences(),
        )

    def access(self) -> AccessInfo:
        """Return account plan/limits through the typed Portfolio gateway."""
        return self.portfolio_gateway.access()

    # Internal composition helpers ------------------------------------
    def _resolve_repository(self, selector: str, project: str | None) -> RepositoryRef:
        return GatewaySelectorResolver(self.portfolio_gateway).resolve_repository(selector, project=project)

    def _targets(self, repo: str | None, project: str | None) -> tuple[RepositoryRef, ...]:
        projects = self.portfolio_gateway.list_projects()
        selected = (
            projects if project is None else (GatewaySelectorResolver(self.portfolio_gateway).resolve_project(project),)
        )
        repos = tuple(
            repo_ref
            for item in selected
            for repo_ref in self.portfolio_gateway.project_detail(item.project_id).repositories
        )
        if repo is None:
            return repos
        return (GatewaySelectorResolver(self.portfolio_gateway).resolve_repository(repo, project=project),)

    def _write_targets(
        self, repo: str | None, project: str | None, scope: AutofixWriteScope | None
    ) -> tuple[RepositoryRef, ...]:
        resolved = scope or AutofixWriteScope()
        mutation = MutationScope.from_args(
            repo,
            project,
            all_repos=resolved.all_repos,
            all_projects=resolved.all_projects,
            operation="mutation",
        )
        if mutation.kind == "all_projects":
            return self._targets(None, None)
        if mutation.kind == "all_repos":
            return self._targets(None, mutation.project)
        return self._targets(mutation.repo, mutation.project)

    def _audit_project(self, project_id: str):
        from enji_guard_cli.audit.ports import AuditProject, AuditRepository, AuditWebsite

        detail = self.portfolio_gateway.project_detail(project_id)
        return AuditProject(
            project_id=detail.project.project_id,
            repositories=tuple(
                AuditRepository(repo.repo_id, repo.full_name or "", repo.connected is True)
                for repo in detail.repositories
            ),
            linked_websites=tuple(
                AuditWebsite(url, tuple(detail.linked_website_repo_ids.get(url, ()))) for url in detail.linked_websites
            ),
        )

    def _select_audits(
        self, catalog: AuditCatalog, selectors: list[str], *, all_audits: bool
    ) -> tuple[AuditDefinition, ...]:
        return choose_audits(catalog, selectors, all_audits=all_audits)

    def _active_runs(self, repo_id: str):
        upstream = self.audit_gateway.active_runs(repo_id).runs
        if self.ledger is None:
            return upstream
        return self.ledger.reconcile(repo_id, upstream, self.audit_gateway.task_detail)

    def _preserve_snapshots(self, repo_id: str, catalog: AuditCatalog) -> None:
        status = self.audit_status(repo_id, catalog=catalog)
        groups = {audit.action_key: audit.metric_group for audit in catalog.published_audits}
        for action_key in status.readable:
            self.audit_gateway.read_audit_snapshot(repo_id, action_key, groups.get(action_key))

    def _start_batch(
        self, repo_id: str, project_id: str, audits: tuple[AuditDefinition, ...], catalog: AuditCatalog
    ) -> dict[str, object]:
        dependencies = StartAuditDependencies(
            make_audit_run_create=lambda target_repo, target_project, action_key, body: AuditRunRequest(
                target_repo, target_project, action_key, body
            ),
            start_audit_run=self.audit_gateway.start_audit_run,
            project_detail=lambda target_project: self._audit_project(target_project),
            runbook=lambda runbook_id: self.audit_gateway.runbook_metadata(runbook_id),
            current_repo_active_runs=self._active_runs,
            record_started_run=self._record_started,
            task_identity=lambda response: (
                cast(AuditRunResult, response).task_id,
                cast(AuditRunResult, response).status,
            ),
        )
        return start_audits_for_target(
            StartAuditsContext(repo_id, project_id, list(audits), catalog),
            dependencies=dependencies,
            get_repo_rerun_state=self.audit_gateway.rerun_state,
        )

    def _record_started(self, context: RecordStartedRunContext) -> None:
        if self.ledger is None:
            return
        self.ledger.record_started(
            new_entry(
                repo_id=context.repo_id,
                project_id=context.project_id,
                audit_key=context.action_key,
                task_id=context.task_id,
                task_status=context.task_status,
                current_head_sha=context.current_head_sha,
                audited_head_sha=context.last_audited_head_sha,
                observed_at=datetime.now(UTC),
                ttl_seconds=getattr(self.ledger, "ttl_seconds", 21_600),
            )
        )


class _AuditStatusReader(AuditStatusReader):
    def __init__(self, application: Application, catalog: AuditCatalog | None = None) -> None:
        self.application = application
        self.catalog = catalog

    def status(self, repo_id: str) -> PortfolioAuditStatus:
        status = self.application.audit_status(repo_id, catalog=self.catalog)
        return PortfolioAuditStatus(
            current_head_sha=status.current_head_sha,
            audited_head_shas={item.audit_key: item.freshness.audited_head_sha for item in status.items},
            audits=audit_status_items(status),
            active_runs=self.application._active_runs(repo_id),
        )


class _AuditStarter(AuditStartPort):
    def __init__(self, application: Application, catalog: AuditCatalog | None = None) -> None:
        self.application = application
        self.catalog = catalog

    def start(self, repo_id: str, project_id: str, action_key: str):
        catalog = self.catalog or self.application.audit_catalog()
        audit = _audit_for_action(catalog, action_key)
        result = cast(
            list[dict[str, object]], self.application._start_batch(repo_id, project_id, (audit,), catalog)["results"]
        )[0]
        return _run_result(result)


def _audit_for_action(catalog: AuditCatalog, action_key: str) -> AuditDefinition:
    if action_key == catalog.recon.action_key:
        return catalog.recon
    return next(item for item in catalog.published_audits if item.action_key == action_key)


def _run_result(result: dict[str, object]):
    from enji_guard_cli.audit.ports import AuditRunResult

    return AuditRunResult(cast(str | None, result.get("task_id")), cast(str | None, result.get("status")))


__all__ = ["Application", "AutofixWriteScope", "EmailPreferencesUpdate"]


def schedule_for_audit(audit: AuditDefinition, schedules: tuple[AuditSchedule, ...]) -> AuditSchedule:
    """Project one configured or unconfigured row for each published audit."""

    current = next((item for item in schedules if item.audit_key == audit.action_key), None)
    return current or AuditSchedule(
        audit_key=audit.action_key,
        enabled=False,
        cadence=None,
        schedule_day=None,
        schedule_day_of_month=None,
        schedule_time=None,
        schedule_time_source=None,
        timezone=None,
    )


def _normalize_autofix_jobs(jobs: tuple[dict[str, JsonValue], ...]) -> tuple[dict[str, JsonValue], ...]:
    """Keep only canonical improvement-job identities and preserve wire extensions."""

    result: list[dict[str, JsonValue]] = []
    seen: set[tuple[str, str]] = set()
    for job in jobs:
        normalized = _normalized_autofix_job(job)
        if normalized is None or normalized[:2] in seen:
            continue
        seen.add(normalized[:2])
        result.append(normalized[2])
    return tuple(result)


def _normalized_autofix_job(job: dict[str, JsonValue]) -> tuple[str, str, dict[str, JsonValue]] | None:
    action = job.get("actionKey") or job.get("kind")
    variant = job.get("variantKey") or job.get("autofixVariantKey")
    if not isinstance(action, str) or not isinstance(variant, str):
        return None
    return action, variant, {**job, "actionKey": action, "variantKey": variant}


def _email_preference_update(update: EmailPreferencesUpdate) -> AuditEmailPreferenceUpdate:
    if update.manual is None and update.scheduled is None:
        raise ValueError("pass --manual or --scheduled")
    return AuditEmailPreferenceUpdate(update.manual, update.scheduled)


def _index_autofix_jobs(jobs: tuple[dict[str, JsonValue], ...]) -> dict[str, dict[str, object]]:
    normalized = _normalize_autofix_jobs(jobs)
    indexed: dict[str, dict[str, object]] = {}
    for job in normalized:
        typed = cast(dict[str, object], job)
        action = job.get("actionKey")
        kind = job.get("kind")
        if isinstance(action, str):
            indexed[action] = typed
        if isinstance(kind, str):
            indexed[kind] = typed
    return indexed
