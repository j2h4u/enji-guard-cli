"""Typed application composition for Audit and Portfolio use-cases.

Delivery code talks to this module (and to :class:`AuthSessionService`) only.
The gateway is the sole place that knows the upstream HTTP vocabulary; this
facade coordinates context use-cases and keeps selectors and write scope
explicit.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from enji_guard_cli.audit import parse_catalog_result
from enji_guard_cli.audit.artifacts import AuditArtifactUnavailableError
from enji_guard_cli.audit.autofixes import definitions as autofix_definitions
from enji_guard_cli.audit.autofixes import select as select_autofixes
from enji_guard_cli.audit.autofixes import set_one
from enji_guard_cli.audit.catalog_observation import AuditCatalogObserver
from enji_guard_cli.audit.email import EmailPreferencesUpdate
from enji_guard_cli.audit.email import list_for_targets as list_email_for_targets
from enji_guard_cli.audit.email import set_for_targets as set_email_for_targets
from enji_guard_cli.audit.errors import AuditMalformedError, AuditNotFoundError, AuditUpstreamError
from enji_guard_cli.audit.ledger import FileAuditLedger
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import (
    AuditAutofixJob,
    AuditAutofixUpdate,
    AuditCatalogResult,
    AuditGatewayPort,
    AuditLedgerPort,
    AuditRun,
    AuditRunResult,
    AuditScheduleUpdate,
    AuditStatus,
    AuditWaitOptions,
    AuditWaitResult,
    MalformedAuditSnapshotError,
)
from enji_guard_cli.audit.preflight import AuditPreflight, build_preflight
from enji_guard_cli.audit.schedules import auto_time_for_targets, list_for_targets, set_for_targets
from enji_guard_cli.audit.start import AuditStartService
from enji_guard_cli.audit.status import build_status
from enji_guard_cli.audit.wait import AuditWaitDependencies, wait_for_completion
from enji_guard_cli.audit.workflows import AuditWorkflowDependencies, choose_audits, read_for_repo
from enji_guard_cli.auth_session.adapters import AuthSessionAdapter
from enji_guard_cli.auth_session.api import AuthError
from enji_guard_cli.auth_session.models import (
    AuthSessionRefreshResult,
    AuthSessionStatus,
    ImportCredentialPayload,
)
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.enji_gateway import AuditGateway, PortfolioGateway
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.portfolio.errors import PortfolioMalformedError, PortfolioNotFoundError, PortfolioUpstreamError
from enji_guard_cli.portfolio.models import (
    AccessInfo,
    AccountPreferences,
    OperationResult,
    ProjectRef,
    ProjectSettings,
    RepositoryRef,
)
from enji_guard_cli.portfolio.ports import (
    AuditStartPort,
    AuditStatusReader,
    PortfolioAuditStatus,
    PortfolioGatewayPort,
    PortfolioTargetService,
)
from enji_guard_cli.portfolio.projects import create_project as create_project_use_case
from enji_guard_cli.portfolio.projects import delete_project as delete_project_use_case
from enji_guard_cli.portfolio.projects import rename_project as rename_project_use_case
from enji_guard_cli.portfolio.recon import recon_after_add
from enji_guard_cli.portfolio.recon import start_recon as start_recon_use_case
from enji_guard_cli.portfolio.repositories import add_repository, move_repository, remove_repository
from enji_guard_cli.portfolio.selectors import GatewayPortfolioTargetService, GatewaySelectorResolver
from enji_guard_cli.portfolio.status import (
    PortfolioOverview,
    PortfolioStatus,
    assemble_overview,
    assemble_status,
    status_for_repo,
)
from enji_guard_cli.runtime_observability.ports import RuntimeAuthPort
from enji_guard_cli.runtime_observability.telemetry import log_event
from enji_guard_cli.settings import RepositorySortName, default_settings


class ApplicationAuthError(Exception):
    """Typed authentication failure exposed by the application facade."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ApplicationCommandError(Exception):
    """Operator-facing failure translated at the application boundary."""

    def __init__(self, code: str, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class ApplicationCatalogChange:
    action_key: str
    changed_fields: tuple[str, ...]
    kind: str


@dataclass(frozen=True, slots=True)
class ApplicationResult:
    payload: object
    catalog_changes: tuple[ApplicationCatalogChange, ...] = ()


@dataclass(frozen=True, slots=True)
class AutofixWriteScope:
    all_repos: bool = False
    all_projects: bool = False


def _catalog_result_context() -> ContextVar[AuditCatalogResult | None]:
    return ContextVar("application_catalog_result", default=None)


@dataclass(slots=True)
class Application:
    """Composition root shared by CLI, MCP, and runtime supervisor."""

    audit_gateway: AuditGatewayPort
    portfolio_gateway: PortfolioGatewayPort
    auth: AuthSessionService
    ledger: AuditLedgerPort | None = None
    catalog_observer: AuditCatalogObserver | None = None
    target_service: PortfolioTargetService | None = None
    runtime_auth: RuntimeAuthPort | None = None
    _catalog_result: ContextVar[AuditCatalogResult | None] = field(default_factory=_catalog_result_context, repr=False)

    def execute(self, action: Callable[[], object]) -> ApplicationResult:
        """Execute one delivery action and translate context failures."""
        self._catalog_result.set(None)
        try:
            payload = action()
        except EnjiApiError as exc:
            raise ApplicationCommandError(exc.code, exc.message, _exit_code_for_error(exc.code)) from exc
        except ApplicationAuthError as exc:
            raise ApplicationCommandError(exc.code, exc.message, _exit_code_for_error(exc.code)) from exc
        except (AuditArtifactUnavailableError, AuditNotFoundError, PortfolioNotFoundError) as exc:
            raise ApplicationCommandError("NOT_FOUND", str(exc), 4) from exc
        except (
            MalformedAuditSnapshotError,
            AuditMalformedError,
            AuditUpstreamError,
            PortfolioMalformedError,
            PortfolioUpstreamError,
        ) as exc:
            raise ApplicationCommandError("UPSTREAM", str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise ApplicationCommandError("VALIDATION", str(exc)) from exc
        catalog_result = self._catalog_result.get()
        changes = () if catalog_result is None else catalog_result.changes
        return ApplicationResult(
            payload,
            tuple(
                ApplicationCatalogChange(change.action_key, change.changed_fields, change.kind) for change in changes
            ),
        )

    @classmethod
    def from_auth_file(cls, auth_file: Path | None = None) -> Application:
        settings = default_settings()
        ledger = FileAuditLedger(
            settings.active_run_ledger.state_file,
            ttl_seconds=settings.active_run_ledger.ttl_seconds,
            lookup_grace_seconds=settings.active_run_ledger.lookup_grace_seconds,
        )
        auth_adapter = AuthSessionAdapter(auth_file, settings=settings, event_sink=log_event)
        auth_service = AuthSessionService(auth_file, settings=settings, event_sink=log_event)
        portfolio_gateway = PortfolioGateway(auth_file, auth_port=auth_adapter)
        return cls(
            AuditGateway(auth_file, auth_port=auth_adapter),
            portfolio_gateway,
            auth_service,
            ledger,
            AuditCatalogObserver(settings.audit_catalog.state_file),
            GatewayPortfolioTargetService(portfolio_gateway),
            auth_adapter,
        )

    def import_cookie(self, raw_cookie: str) -> ImportCredentialPayload:
        try:
            return self.auth.import_cookie(raw_cookie)
        except AuthError as exc:
            raise ApplicationAuthError(exc.code, exc.message) from exc

    def import_bearer(self, raw_token: str) -> ImportCredentialPayload:
        try:
            return self.auth.import_bearer_token(raw_token)
        except AuthError as exc:
            raise ApplicationAuthError(exc.code, exc.message) from exc

    def auth_status(self) -> AuthSessionStatus:
        try:
            return self.auth.status()
        except AuthError as exc:
            raise ApplicationAuthError(exc.code, exc.message) from exc

    def auth_refresh(self) -> AuthSessionRefreshResult:
        try:
            return self.auth.refresh()
        except AuthError as exc:
            raise ApplicationAuthError(exc.code, exc.message) from exc

    def runtime_auth_port(self) -> RuntimeAuthPort:
        if self.runtime_auth is None:
            raise RuntimeError("runtime auth is not configured")
        return self.runtime_auth

    # Catalog and Audit -------------------------------------------------
    def catalog(self) -> AuditCatalogResult:
        """Fetch the live catalog once; ``changes`` is the typed observation hook."""
        result = self.audit_gateway.catalog()
        if self.catalog_observer is not None:
            result = self.catalog_observer.observe(result)
        self._catalog_result.set(result)
        return result

    def audit_catalog(self) -> AuditCatalog:
        return parse_catalog_result(self.catalog())

    def audit_status(self, repo_id: str, *, catalog: AuditCatalog | None = None) -> AuditStatus:
        return self._audit_status_with_runs(repo_id, catalog=catalog)[0]

    def _audit_status_with_runs(
        self, repo_id: str, *, catalog: AuditCatalog | None = None
    ) -> tuple[AuditStatus, tuple[AuditRun, ...]]:
        definitions = catalog if catalog is not None else self.audit_catalog()
        active_runs = self._active_runs(repo_id)
        return build_status(
            repo_id,
            definitions,
            self.audit_gateway.task_links(repo_id).links,
            active_runs,
            self.audit_gateway.rerun_state(repo_id),
        ), active_runs

    def audit_start(
        self, repo: str, project: str | None = None, selectors: list[str] | None = None, *, all_audits: bool = False
    ) -> object:
        target = self._resolve_repository(repo, project)
        catalog = self.audit_catalog()
        selected = self._select_audits(catalog, selectors or [], all_audits=all_audits)
        batch = self._audit_start_service().start(target.repo_id, target.project_id, selected, catalog)
        return {"repo_id": target.repo_id, "project_id": target.project_id, **batch}

    def audit_start_one(self, repo_id: str, project_id: str, audit: AuditDefinition) -> object:
        catalog = self.audit_catalog()
        batch = self._audit_start_service().start(repo_id, project_id, (audit,), catalog)
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
        timeout_seconds: float | None = None,
        heartbeat: object | None = None,
    ) -> AuditWaitResult:
        target = self._resolve_repository(repo, project)
        settings = default_settings().audit_wait
        selected = AuditWaitOptions(
            settings.poll_seconds,
            settings.timeout_seconds if timeout_seconds is None else timeout_seconds,
            settings.heartbeat_seconds,
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

    def portfolio_overview(self, project: str | None = None, sort: RepositorySortName = "default") -> PortfolioOverview:
        return assemble_overview(gateway=self.portfolio_gateway, project=project, sort=sort)

    def repository_status(self, repo: str, project: str | None = None) -> object:
        catalog = self.audit_catalog()
        return status_for_repo(repo, project, gateway=self.portfolio_gateway, audits=_AuditStatusReader(self, catalog))

    # Schedules, autofixes, preferences --------------------------------
    def list_schedules(self, repo: str | None = None, project: str | None = None) -> tuple[object, ...]:
        catalog = self.audit_catalog()
        results = list_for_targets(
            self._targets(repo, project),
            tuple(audit.action_key for audit in catalog.published_audits),
            self.audit_gateway,
        )
        return tuple(schedule for result in results for schedule in result.schedules)

    def set_schedules(  # noqa: PLR0913
        self,
        repo: str | None,
        project: str | None,
        *,
        enabled: bool | None = None,
        cadence: str | None = None,
        timezone: str | None = None,
        scope: AutofixWriteScope | None = None,
    ) -> tuple[object, ...]:
        catalog = self.audit_catalog()
        return set_for_targets(
            self._write_targets(repo, project, scope),
            tuple(audit.action_key for audit in catalog.published_audits),
            AuditScheduleUpdate(enabled=enabled, cadence=cadence, timezone=timezone),
            self.audit_gateway,
        )

    def schedule_auto_time(
        self, repo: str | None, project: str | None = None, *, scope: AutofixWriteScope | None = None
    ) -> tuple[object, ...]:
        catalog = self.audit_catalog()
        return auto_time_for_targets(
            self._write_targets(repo, project, scope),
            tuple(audit.action_key for audit in catalog.published_audits),
            self.audit_gateway,
        )

    def list_autofixes(self, repo: str | None = None, project: str | None = None) -> tuple[object, ...]:
        catalog = self.catalog()
        definitions = autofix_definitions(catalog)
        return tuple(
            (target, definitions, _normalize_autofix_jobs(self.audit_gateway.list_autofix_jobs(target.repo_id)))
            for target in self._targets(repo, project)
        )

    def set_autofixes(  # noqa: PLR0913
        self,
        repo: str | None,
        project: str | None,
        selectors: list[str],
        *,
        enabled: bool | None = None,
        cadence: str | None = None,
        timezone: str | None = None,
        scope: AutofixWriteScope | None = None,
    ) -> tuple[object, ...]:
        catalog = self.catalog()
        selected = select_autofixes(selectors, autofix_definitions(catalog))
        result: list[object] = []
        for target in self._write_targets(repo, project, scope):
            jobs = _index_autofix_jobs(self.audit_gateway.list_autofix_jobs(target.repo_id))
            for definition in selected:
                existing = jobs.get(definition.action_key) or jobs.get(definition.kind or definition.selector)
                outcome = set_one(
                    definition,
                    existing,
                    AuditAutofixUpdate(enabled, cadence, timezone),
                    lambda kind, job, repo_id=target.repo_id: self.audit_gateway.set_autofix_job(repo_id, kind, job),
                )
                result.append(outcome)
        return tuple(result)

    def list_email_preferences(self, repo: str | None = None, project: str | None = None) -> tuple[object, ...]:
        catalog = self.audit_catalog()
        keys = tuple(audit.action_key for audit in catalog.published_audits)
        return list_email_for_targets(self._targets(repo, project), keys, self.audit_gateway)

    def set_email_preferences(
        self,
        repo: str | None,
        project: str | None,
        update: EmailPreferencesUpdate,
        *,
        scope: AutofixWriteScope | None = None,
    ) -> tuple[object, ...]:
        keys = tuple(a.action_key for a in self.audit_catalog().published_audits)
        return set_email_for_targets(self._write_targets(repo, project, scope), keys, update, self.audit_gateway)

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
        resolver = self.target_service or GatewaySelectorResolver(self.portfolio_gateway)
        return resolver.resolve_repository(selector, project=project)

    def _targets(self, repo: str | None, project: str | None) -> tuple[RepositoryRef, ...]:
        resolver = self.target_service
        if resolver is not None:
            return resolver.targets(repo, project)
        return GatewayPortfolioTargetService(self.portfolio_gateway).targets(repo, project)

    def _write_targets(
        self, repo: str | None, project: str | None, scope: AutofixWriteScope | None
    ) -> tuple[RepositoryRef, ...]:
        resolved = scope or AutofixWriteScope()
        resolver = self.target_service or GatewayPortfolioTargetService(self.portfolio_gateway)
        return resolver.write_targets(
            repo,
            project,
            all_repos=resolved.all_repos,
            all_projects=resolved.all_projects,
            operation="mutation",
        )

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
        return self._audit_start_service().active_runs(repo_id)

    def _audit_start_service(self) -> AuditStartService:
        return AuditStartService(self.audit_gateway, self.ledger, self._audit_project)


class _AuditStatusReader(AuditStatusReader):
    def __init__(self, application: Application, catalog: AuditCatalog | None = None) -> None:
        self.application = application
        self.catalog = catalog

    def status(self, repo_id: str) -> PortfolioAuditStatus:
        status, active_runs = self.application._audit_status_with_runs(repo_id, catalog=self.catalog)
        return PortfolioAuditStatus.from_audit_status(status, active_runs=cast(tuple, active_runs))


class _AuditStarter(AuditStartPort):
    def __init__(self, application: Application, catalog: AuditCatalog | None = None) -> None:
        self.application = application
        self.catalog = catalog

    def start(self, repo_id: str, project_id: str, action_key: str):
        catalog = self.catalog or self.application.audit_catalog()
        audit = _audit_for_action(catalog, action_key)
        result = cast(
            list[dict[str, object]],
            self.application._audit_start_service().start(repo_id, project_id, (audit,), catalog)["results"],
        )[0]
        return _run_result(result)


def _audit_for_action(catalog: AuditCatalog, action_key: str) -> AuditDefinition:
    if action_key == catalog.recon.action_key:
        return catalog.recon
    audit = next((item for item in catalog.published_audits if item.action_key == action_key), None)
    if audit is None:
        raise AuditNotFoundError(f"audit action is no longer published: {action_key}")
    return audit


def _run_result(result: dict[str, object]):

    return AuditRunResult(cast(str | None, result.get("task_id")), cast(str | None, result.get("status")))


def _exit_code_for_error(code: str) -> int:
    if code.startswith("AUTH_"):
        return 3
    if code in {"NOT_FOUND", "BAD_SELECTOR"}:
        return 4
    return 1


__all__ = [
    "Application",
    "ApplicationAuthError",
    "ApplicationCatalogChange",
    "ApplicationCommandError",
    "ApplicationResult",
    "AutofixWriteScope",
    "EmailPreferencesUpdate",
    "PortfolioOverview",
]


def _normalize_autofix_jobs(jobs: tuple[AuditAutofixJob, ...]) -> tuple[AuditAutofixJob, ...]:
    """Keep one canonical job for each action/variant identity."""

    result: list[AuditAutofixJob] = []
    seen: set[tuple[str, str]] = set()
    for job in jobs:
        identity = (job.action_key, job.variant_key)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(job)
    return tuple(result)


def _index_autofix_jobs(jobs: tuple[AuditAutofixJob, ...]) -> dict[str, AuditAutofixJob]:
    indexed: dict[str, AuditAutofixJob] = {}
    for job in _normalize_autofix_jobs(jobs):
        indexed[job.action_key] = job
        if job.kind is not None:
            indexed[job.kind] = job
    return indexed
