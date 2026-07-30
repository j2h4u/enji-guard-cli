"""Recurring audit subscriptions: schedules, improvement jobs, and email."""

from collections.abc import Callable
from dataclasses import dataclass

from enji_guard_cli.application.catalog import AuditCatalogService
from enji_guard_cli.application.mutations import (
    BatchMutationResult,
    MutationDecision,
    MutationOperationalError,
    MutationReason,
    MutationTargetView,
    execute_batch,
)
from enji_guard_cli.application.portfolio_views import RepositoryRefView, repository_ref_view
from enji_guard_cli.application.subscription_views import (
    AuditScheduleView,
    ImprovementDefinitionView,
    ImprovementJobView,
    improvement_definition_view,
    improvement_job_view,
    schedule_view,
)
from enji_guard_cli.audit.email import EmailPreferencesUpdate
from enji_guard_cli.audit.email import list_for_targets as list_email_for_targets
from enji_guard_cli.audit.email import validate_update as validate_email_update
from enji_guard_cli.audit.errors import AuditNotFoundError, AuditUpstreamError
from enji_guard_cli.audit.improvement_jobs import definitions as improvement_definitions
from enji_guard_cli.audit.improvement_jobs import select as select_improvements
from enji_guard_cli.audit.improvement_jobs import set_one, validate_improvement_job_update
from enji_guard_cli.audit.ports import (
    AuditGatewayPort,
    AuditScheduleUpdate,
    ImprovementDefinition,
    ImprovementJob,
    ImprovementJobUpdate,
)
from enji_guard_cli.audit.schedules import (
    auto_time,
    list_for_targets,
    plan_schedule_update,
    validate_schedule_update,
)
from enji_guard_cli.audit.scheduling import CADENCES
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.portfolio.errors import PortfolioNotFoundError, PortfolioUpstreamError
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
class ImprovementJobListingItem:
    definition: ImprovementDefinitionView
    job: ImprovementJobView | None


@dataclass(frozen=True, slots=True)
class ImprovementJobListing:
    repository: RepositoryRefView
    items: tuple[ImprovementJobListingItem, ...]


@dataclass(frozen=True, slots=True)
class SubscriptionWriteScope:
    all_repos: bool = False
    all_projects: bool = False


@dataclass(frozen=True, slots=True)
class ScheduleWriteRequest:
    repo: str | None
    project: str | None
    enabled: bool | None = None
    cadence: str | None = None
    timezone: str | None = None
    scope: SubscriptionWriteScope | None = None


@dataclass(frozen=True, slots=True)
class ImprovementJobWriteRequest:
    repo: str | None
    project: str | None
    selectors: tuple[str, ...]
    enabled: bool | None = None
    automatic_execution: bool | None = None
    frequency: str | None = None
    days_of_week: tuple[str, ...] | None = None
    schedule_time: str | None = None
    timezone: str | None = None
    scope: SubscriptionWriteScope | None = None


@dataclass(frozen=True, slots=True)
class EmailPreferencesWriteRequest:
    repo: str | None
    project: str | None
    manual: bool | None = None
    scheduled: bool | None = None
    scope: SubscriptionWriteScope | None = None


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

    def set_schedules(self, request: ScheduleWriteRequest) -> BatchMutationResult:
        """Apply one schedule change, given the operator's raw argv values."""
        update = AuditScheduleUpdate(enabled=request.enabled, cadence=request.cadence, timezone=request.timezone)
        validate_schedule_update(update)
        keys = self._published_keys()
        targets = self._write_targets(request.repo, request.project, request.scope)
        decisions: list[MutationDecision] = []
        for target in targets:
            existing = {item.audit_key: item for item in self.gateway.list_schedules(target.repo_id)}
            for audit_key in keys:
                current = existing.get(audit_key)
                desired = plan_schedule_update(current, audit_key, update)
                mutation_target = _mutation_target(target, audit_key.removeprefix("audit."))
                if desired is None:
                    decisions.append(_unchanged(mutation_target, MutationReason.NOT_CONFIGURED))
                elif current == desired:
                    decisions.append(_unchanged(mutation_target, MutationReason.ALREADY_EFFECTIVE))
                else:
                    decisions.append(
                        _changed(
                            mutation_target,
                            lambda repo_id=target.repo_id, key=audit_key, item=desired: self.gateway.set_schedule(
                                repo_id, key, item
                            ),
                        )
                    )
        return execute_batch(decisions)

    def schedule_auto_time(
        self, repo: str | None, project: str | None = None, *, scope: SubscriptionWriteScope | None = None
    ) -> BatchMutationResult:
        keys = self._published_keys()
        published = frozenset(keys)
        targets = self._write_targets(repo, project, scope)
        decisions: list[MutationDecision] = []
        for target in targets:
            for current in self.gateway.list_schedules(target.repo_id):
                if current.audit_key not in published:
                    continue
                desired = auto_time(current)
                mutation_target = _mutation_target(target, current.audit_key.removeprefix("audit."))
                if desired == current:
                    decisions.append(_unchanged(mutation_target, MutationReason.ALREADY_EFFECTIVE))
                else:
                    decisions.append(
                        _changed(
                            mutation_target,
                            lambda repo_id=target.repo_id, key=current.audit_key, item=desired: (
                                self.gateway.set_schedule(repo_id, key, item)
                            ),
                        )
                    )
        return execute_batch(decisions)

    def list_improvement_jobs(
        self, repo: str | None = None, project: str | None = None
    ) -> tuple[ImprovementJobListing, ...]:
        definitions = improvement_definitions(self.catalog.catalog())
        targets = self.targets.targets(repo, project)

        def read_target(target: RepositoryRef) -> ImprovementJobListing:
            jobs = _index_improvement_jobs(self.gateway.list_improvement_jobs(target.repo_id))
            items = tuple(
                _improvement_item(
                    definition,
                    jobs.get(definition.action_key) or jobs.get(definition.kind or definition.selector),
                )
                for definition in definitions
            )
            return ImprovementJobListing(repository_ref_view(target), items)

        return self.fanout.map(targets, read_target)

    def set_improvement_jobs(self, request: ImprovementJobWriteRequest) -> BatchMutationResult:
        """Apply one improvement-job change, given the operator's argv values."""
        update = ImprovementJobUpdate(
            enabled=request.enabled,
            frequency=request.frequency,
            timezone=request.timezone,
            days_of_week=request.days_of_week,
            schedule_time=request.schedule_time,
            automatic_execution=request.automatic_execution,
        )
        validate_improvement_job_update(update)
        selected = select_improvements(request.selectors, improvement_definitions(self.catalog.catalog()))
        targets = self._write_targets(request.repo, request.project, request.scope)
        decisions: list[MutationDecision] = []
        for target in targets:
            jobs = _index_improvement_jobs(self.gateway.list_improvement_jobs(target.repo_id))
            for definition in selected:
                existing = jobs.get(definition.action_key) or jobs.get(definition.kind or definition.selector)
                planned = set_one(
                    definition,
                    existing,
                    update,
                    lambda _kind, job: job,
                )
                mutation_target = _mutation_target(target, definition.selector)
                if planned.status == "unchanged":
                    reason = MutationReason.NOT_CONFIGURED if existing is None else MutationReason.ALREADY_EFFECTIVE
                    decisions.append(_unchanged(mutation_target, reason))
                    continue
                if planned.job is None:
                    raise RuntimeError("changed improvement-job plan has no desired job")
                decisions.append(
                    _changed(
                        mutation_target,
                        lambda repo_id=target.repo_id, kind=definition.kind or definition.selector, job=planned.job: (
                            self.gateway.set_improvement_job(repo_id, kind, job)
                        ),
                    )
                )
        return execute_batch(decisions)

    def list_email_preferences(self, repo: str | None = None, project: str | None = None) -> tuple[object, ...]:
        keys = self._published_keys()
        return list_email_for_targets(self.targets.targets(repo, project), keys, self.gateway, self.fanout)

    def set_email_preferences(self, request: EmailPreferencesWriteRequest) -> BatchMutationResult:
        """Apply one completion-email change, given the operator's argv values."""
        update = EmailPreferencesUpdate(manual=request.manual, scheduled=request.scheduled)
        typed = validate_email_update(update)
        keys = self._published_keys()
        targets = self._write_targets(request.repo, request.project, request.scope)
        decisions: list[MutationDecision] = []
        for target in targets:
            for audit_key in keys:
                current = self.gateway.get_email_preferences(target.repo_id, audit_key)
                mutation_target = _mutation_target(target, audit_key.removeprefix("audit."))
                if _email_effective(current.manual, typed.manual) and _email_effective(
                    current.scheduled, typed.scheduled
                ):
                    decisions.append(_unchanged(mutation_target, MutationReason.ALREADY_EFFECTIVE))
                else:
                    decisions.append(
                        _changed(
                            mutation_target,
                            lambda repo_id=target.repo_id, key=audit_key: self.gateway.set_email_preference(
                                repo_id, key, typed
                            ),
                        )
                    )
        return execute_batch(decisions)

    def _published_keys(self) -> tuple[str, ...]:
        return tuple(audit.action_key for audit in self.catalog.audits().published_audits)

    def _write_targets(
        self, repo: str | None, project: str | None, scope: SubscriptionWriteScope | None
    ) -> tuple[RepositoryRef, ...]:
        resolved = scope or SubscriptionWriteScope()
        return self.targets.write_targets(
            repo,
            project,
            all_repos=resolved.all_repos,
            all_projects=resolved.all_projects,
            operation="mutation",
        )


def _improvement_item(definition: ImprovementDefinition, job: ImprovementJob | None) -> ImprovementJobListingItem:
    return ImprovementJobListingItem(
        improvement_definition_view(definition),
        improvement_job_view(job) if job is not None else None,
    )


def _normalize_improvement_jobs(jobs: tuple[ImprovementJob, ...]) -> tuple[ImprovementJob, ...]:
    """Keep one canonical job for each action/variant identity."""
    result: list[ImprovementJob] = []
    seen: set[tuple[str, str]] = set()
    for job in jobs:
        identity = (job.action_key, job.variant_key)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(job)
    return tuple(result)


def _index_improvement_jobs(jobs: tuple[ImprovementJob, ...]) -> dict[str, ImprovementJob]:
    indexed: dict[str, ImprovementJob] = {}
    for job in _normalize_improvement_jobs(jobs):
        indexed[job.action_key] = job
        if job.kind is not None:
            indexed[job.kind] = job
    return indexed


def _mutation_target(target: RepositoryRef, selector: str) -> MutationTargetView:
    return MutationTargetView(repository_ref_view(target), selector)


def _unchanged(target: MutationTargetView, reason: MutationReason) -> MutationDecision:
    return MutationDecision(target, "unchanged", reason, lambda: None)


def _changed(target: MutationTargetView, write: Callable[[], object]) -> MutationDecision:
    return MutationDecision(target, "changed", MutationReason.APPLIED, _operational_write(write))


def _operational_write(write: Callable[[], object]) -> Callable[[], None]:
    """Convert only known upstream/storage failures to the executor's failure value."""

    def apply() -> None:
        try:
            write()
        except EnjiApiError as exc:
            raise MutationOperationalError(exc.code, exc.message, outcome_unknown=True) from exc
        except (AuditNotFoundError, PortfolioNotFoundError) as exc:
            raise MutationOperationalError("NOT_FOUND", str(exc), outcome_unknown=True) from exc
        except (AuditUpstreamError, PortfolioUpstreamError, OSError) as exc:
            raise MutationOperationalError("UPSTREAM", str(exc), outcome_unknown=True) from exc

    return apply


def _email_effective(current: bool | None, requested: bool | None) -> bool:
    """A field the operator did not request never makes an email setting dirty."""
    return requested is None or current == requested


__all__ = [
    "AUDIT_CADENCES",
    "ImprovementJobListing",
    "ImprovementJobListingItem",
    "ImprovementJobWriteRequest",
    "ScheduleListing",
    "SubscriptionWriteScope",
    "SubscriptionsFacade",
]
