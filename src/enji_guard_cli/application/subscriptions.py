"""Recurring audit subscriptions: schedules, improvement jobs, and email."""

from dataclasses import dataclass

from enji_guard_cli.application.catalog import AuditCatalogService
from enji_guard_cli.application.portfolio_views import RepositoryRefView, repository_ref_view
from enji_guard_cli.application.subscription_views import (
    AuditAutofixDefinitionView,
    AuditAutofixJobView,
    AuditScheduleView,
    autofix_definition_view,
    autofix_job_view,
    schedule_view,
)
from enji_guard_cli.audit.autofixes import definitions as autofix_definitions
from enji_guard_cli.audit.autofixes import select as select_autofixes
from enji_guard_cli.audit.autofixes import set_one
from enji_guard_cli.audit.email import EmailPreferencesUpdate
from enji_guard_cli.audit.email import list_for_targets as list_email_for_targets
from enji_guard_cli.audit.email import set_for_targets as set_email_for_targets
from enji_guard_cli.audit.ports import (
    AuditAutofixDefinition,
    AuditAutofixJob,
    AuditAutofixUpdate,
    AuditGatewayPort,
    AuditScheduleUpdate,
)
from enji_guard_cli.audit.schedules import CADENCES, auto_time_for_targets, list_for_targets, set_for_targets
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.portfolio.models import RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioTargetService

AUDIT_CADENCES: tuple[str, ...] = tuple(sorted(CADENCES))
"""Run cadences an operator may ask for, in the order help text lists them.

Published here so a delivery surface can build its ``--frequency`` help
without reading an audit module.
"""


@dataclass(frozen=True, slots=True)
class ScheduleListing:
    repository: RepositoryRefView
    schedules: tuple[AuditScheduleView, ...]


@dataclass(frozen=True, slots=True)
class AutofixListingItem:
    definition: AuditAutofixDefinitionView
    job: AuditAutofixJobView | None


@dataclass(frozen=True, slots=True)
class AutofixListing:
    repository: RepositoryRefView
    items: tuple[AutofixListingItem, ...]


@dataclass(frozen=True, slots=True)
class AutofixWriteScope:
    all_repos: bool = False
    all_projects: bool = False


@dataclass(frozen=True, slots=True)
class SubscriptionsFacade:
    """Read and write the recurring settings attached to published audits."""

    catalog: AuditCatalogService
    gateway: AuditGatewayPort
    targets: PortfolioTargetService
    fanout: BoundedFanout

    def list_schedules(self, repo: str | None = None, project: str | None = None) -> tuple[ScheduleListing, ...]:
        keys = self._published_keys()
        targets = self.targets.targets(repo, project)
        results = list_for_targets(targets, keys, self.gateway, self.fanout)
        by_repo_id = {result.repo_id: result.schedules for result in results}
        return tuple(
            ScheduleListing(
                repository_ref_view(target),
                tuple(schedule_view(schedule) for schedule in by_repo_id[target.repo_id]),
            )
            for target in targets
        )

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
        """Apply one schedule change, given the operator's raw argv values."""
        keys = self._published_keys()
        update = AuditScheduleUpdate(enabled=enabled, cadence=cadence, timezone=timezone)
        return set_for_targets(self._write_targets(repo, project, scope), keys, update, self.gateway)

    def schedule_auto_time(
        self, repo: str | None, project: str | None = None, *, scope: AutofixWriteScope | None = None
    ) -> tuple[object, ...]:
        keys = self._published_keys()
        return auto_time_for_targets(self._write_targets(repo, project, scope), keys, self.gateway)

    def list_autofixes(self, repo: str | None = None, project: str | None = None) -> tuple[AutofixListing, ...]:
        definitions = autofix_definitions(self.catalog.catalog())
        targets = self.targets.targets(repo, project)

        def read_target(target: RepositoryRef) -> AutofixListing:
            jobs = _index_autofix_jobs(self.gateway.list_autofix_jobs(target.repo_id))
            items = tuple(
                _autofix_item(
                    definition,
                    jobs.get(definition.action_key) or jobs.get(definition.kind or definition.selector),
                )
                for definition in definitions
            )
            return AutofixListing(repository_ref_view(target), items)

        return self.fanout.map(targets, read_target)

    def set_autofixes(  # noqa: PLR0913
        self,
        repo: str | None,
        project: str | None,
        selectors: list[str],
        *,
        enabled: bool | None = None,
        frequency: str | None = None,
        timezone: str | None = None,
        scope: AutofixWriteScope | None = None,
    ) -> tuple[object, ...]:
        """Apply one improvement-job change, given the operator's argv values."""
        update = AuditAutofixUpdate(enabled=enabled, frequency=frequency, timezone=timezone)
        selected = select_autofixes(selectors, autofix_definitions(self.catalog.catalog()))
        result: list[object] = []
        for target in self._write_targets(repo, project, scope):
            jobs = _index_autofix_jobs(self.gateway.list_autofix_jobs(target.repo_id))
            for definition in selected:
                existing = jobs.get(definition.action_key) or jobs.get(definition.kind or definition.selector)
                outcome = set_one(
                    definition,
                    existing,
                    update,
                    lambda kind, job, repo_id=target.repo_id: self.gateway.set_autofix_job(repo_id, kind, job),
                )
                result.append(outcome)
        return tuple(result)

    def list_email_preferences(self, repo: str | None = None, project: str | None = None) -> tuple[object, ...]:
        keys = self._published_keys()
        return list_email_for_targets(self.targets.targets(repo, project), keys, self.gateway, self.fanout)

    def set_email_preferences(
        self,
        repo: str | None,
        project: str | None,
        *,
        manual: bool | None = None,
        scheduled: bool | None = None,
        scope: AutofixWriteScope | None = None,
    ) -> tuple[object, ...]:
        """Apply one completion-email change, given the operator's argv values."""
        keys = self._published_keys()
        update = EmailPreferencesUpdate(manual=manual, scheduled=scheduled)
        return set_email_for_targets(self._write_targets(repo, project, scope), keys, update, self.gateway)

    def _published_keys(self) -> tuple[str, ...]:
        return tuple(audit.action_key for audit in self.catalog.audits().published_audits)

    def _write_targets(
        self, repo: str | None, project: str | None, scope: AutofixWriteScope | None
    ) -> tuple[RepositoryRef, ...]:
        resolved = scope or AutofixWriteScope()
        return self.targets.write_targets(
            repo,
            project,
            all_repos=resolved.all_repos,
            all_projects=resolved.all_projects,
            operation="mutation",
        )


def _autofix_item(definition: AuditAutofixDefinition, job: AuditAutofixJob | None) -> AutofixListingItem:
    return AutofixListingItem(
        autofix_definition_view(definition),
        autofix_job_view(job) if job is not None else None,
    )


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


__all__ = [
    "AUDIT_CADENCES",
    "AutofixListing",
    "AutofixListingItem",
    "AutofixWriteScope",
    "ScheduleListing",
    "SubscriptionsFacade",
]
