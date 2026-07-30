"""Explicit test construction of the application facades over recording ports.

Every collaborator a facade needs is required in production, so tests supply
one too.  :class:`ApplicationStubs` wires the real facade tree that production
composition wires, and the recording fakes below stand in for the *ports*
underneath it.  Recording at the port boundary is deliberate: it is the only
place where a facade that translated its arguments wrongly becomes visible.
Each fake therefore stores the exact arguments it was handed -- never
``*args, **kwargs`` -- so a swapped or dropped argument fails a test.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import cast

from enji_guard_cli.application import (
    Application,
    ApplicationLifecyclePort,
    ApplicationRunner,
    AuditCatalogService,
    AuditFacade,
    AuditProjectSource,
    AuthFacade,
    CatalogObservationScope,
    GitLabFacade,
    PortfolioFacade,
    SubscriptionsFacade,
)
from enji_guard_cli.audit.catalog_observation import AuditCatalogObservationPort
from enji_guard_cli.audit.ports import (
    AuditArtifact,
    AuditCatalogAction,
    AuditCatalogResult,
    AuditEmailPreference,
    AuditEmailPreferenceUpdate,
    AuditGatewayPort,
    AuditLedgerEntry,
    AuditLedgerPort,
    AuditReportRef,
    AuditRerunState,
    AuditRun,
    AuditRunbookMetadata,
    AuditRunRequest,
    AuditRunResult,
    AuditRunsResult,
    AuditSchedule,
    AuditTaskDetail,
    AuditTaskLink,
    AuditTaskLinksResult,
    CatalogImprovement,
    ImprovementJob,
)
from enji_guard_cli.auth_session.models import AuthSessionStatus, ImportCredentialPayload
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.gitlab.models import (
    GitLabCredentialsQuery,
    GitLabCredentialsResult,
    GitLabProjectsQuery,
    GitLabProjectsResult,
)
from enji_guard_cli.gitlab.ports import GitLabDiscoveryPort
from enji_guard_cli.portfolio.models import (
    AccessInfo,
    AccessLimits,
    AccountPreferences,
    MovePreflight,
    PortfolioActiveRun,
    ProjectDetail,
    ProjectRef,
    RepositoryIdentity,
    RepositoryProvider,
    RepositoryRef,
)
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort, PortfolioTargetService
from enji_guard_cli.portfolio.selectors import resolve_project, resolve_repository
from enji_guard_cli.settings import default_settings


class UnobservedCatalog:
    """Catalog observation that reports exactly what the gateway returned."""

    def observe(self, result: AuditCatalogResult) -> AuditCatalogResult:
        return result


class PassthroughLedger:
    """Ledger that keeps upstream active runs exactly as reported."""

    def __init__(self) -> None:
        self.started: list[AuditLedgerEntry] = []

    def reconcile(self, _repo_id: str, upstream: tuple[AuditRun, ...], _task_detail: object) -> tuple[AuditRun, ...]:
        return upstream

    def record_started(self, entry: AuditLedgerEntry) -> None:
        self.started.append(entry)


class RecordingLifecycle:
    """Lifecycle seam that records how often composition released it."""

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


@dataclass(frozen=True, slots=True)
class ApplicationStubs:
    """Collaborators for one facade tree under test; unset ones stay inert."""

    audit_gateway: object = field(default_factory=object)
    portfolio_gateway: object = field(default_factory=object)
    auth: object = field(default_factory=object)
    ledger: object = field(default_factory=PassthroughLedger)
    catalog_observer: object = field(default_factory=UnobservedCatalog)
    target_service: object = field(default_factory=object)
    gitlab_gateway: object = field(default_factory=object)
    lifecycle: object = field(default_factory=RecordingLifecycle)

    def build(self) -> Application:
        """Wire the same facade tree production composition wires."""
        audit_gateway = cast(AuditGatewayPort, self.audit_gateway)
        portfolio_gateway = cast(PortfolioGatewayPort, self.portfolio_gateway)
        targets = cast(PortfolioTargetService, self.target_service)
        fanout = BoundedFanout(default_settings().fanout)
        scope = CatalogObservationScope()
        catalog = AuditCatalogService(audit_gateway, cast(AuditCatalogObservationPort, self.catalog_observer), scope)
        audit = AuditFacade(
            catalog=catalog,
            gateway=audit_gateway,
            ledger=cast(AuditLedgerPort, self.ledger),
            targets=targets,
            project_source=AuditProjectSource(portfolio_gateway),
            fanout=fanout,
        )
        return Application(
            runner=ApplicationRunner(scope, cast(ApplicationLifecyclePort, self.lifecycle)),
            catalog=catalog,
            auth=AuthFacade(cast(AuthSessionService, self.auth)),
            audit=audit,
            subscriptions=SubscriptionsFacade(catalog=catalog, gateway=audit_gateway, targets=targets, fanout=fanout),
            portfolio=PortfolioFacade(
                gateway=portfolio_gateway, targets=targets, catalog=catalog, audits=audit, fanout=fanout
            ),
            gitlab=GitLabFacade(cast(GitLabDiscoveryPort, self.gitlab_gateway)),
        )


PETS = ProjectRef("p1", "Pets")
DEFAULT_PREFERENCES = AccountPreferences("en")
ALLOWED_MOVE = MovePreflight()
BIRDS = ProjectRef("p2", "Birds")


def repository(  # noqa: PLR0913
    locator: str = "acme/cat",
    *,
    repo_id: str = "r1",
    project: ProjectRef = PETS,
    host: str = "github.com",
    provider: RepositoryProvider = RepositoryProvider.GITHUB,
    scores: Mapping[str, float | int | None] | None = None,
    recon_done: bool | None = None,
    connected: bool | None = True,
) -> RepositoryRef:
    """Build one repository reference with a valid provider identity."""
    return RepositoryRef(
        repo_id,
        project.project_id,
        project.name,
        RepositoryIdentity(provider, locator, host),
        web_url=f"https://{host}/{locator}",
        provider_repo_id=f"provider-{repo_id}",
        connected=connected,
        recon_done=recon_done,
        scores=dict(scores or {}),
    )


REPOSITORY = repository()

CATALOG = AuditCatalogResult(
    actions=(
        AuditCatalogAction("audit.recon", "Recon", "workflow", "draft", None, "recon", "runbook-recon", "recon", "1"),
        AuditCatalogAction(
            "audit.security", "Security", "audit", "published", "vulns", "audit", "runbook-security", "security", "1"
        ),
        AuditCatalogAction(
            "audit.tests", "Tests", "audit", "published", "tests", "audit", "runbook-tests", "tests", "1"
        ),
    ),
    improvements=(
        CatalogImprovement("improvement.vuln-fix", "default", "Vuln fix", None, "runbook-vuln", "published", 1),
        CatalogImprovement("improvement.test-writing", "default", "Tests", None, "runbook-tests", "published", 2),
    ),
)
"""Two published audits plus the two improvement jobs their relationships allow."""


@dataclass(frozen=True, slots=True)
class WriteTargetsCall:
    """One expansion of an explicit mutation scope, exactly as requested."""

    repo: str | None
    project: str | None
    all_repos: bool
    all_projects: bool
    operation: str


@dataclass(frozen=True, slots=True)
class ReadTargetsCall:
    repo: str | None
    project: str | None


@dataclass(frozen=True, slots=True)
class ResolveRepositoryCall:
    selector: str
    project: str | None


class RecordingTargetService:
    """Portfolio target selection that records every scope it was asked for.

    Scope is the whole safety story of a batch write, so this fake keeps
    ``all_repos``, ``all_projects`` and ``operation`` as separate recorded
    fields rather than collapsing them into one opaque call tuple.
    """

    def __init__(
        self,
        repositories: Sequence[RepositoryRef] = (REPOSITORY,),
        projects: Sequence[ProjectRef] = (PETS,),
    ) -> None:
        self.repositories = tuple(repositories)
        self.projects = tuple(projects)
        self.write_targets_calls: list[WriteTargetsCall] = []
        self.read_targets_calls: list[ReadTargetsCall] = []
        self.resolve_repository_calls: list[ResolveRepositoryCall] = []
        self.resolve_project_calls: list[str | None] = []

    def resolve_project(self, selector: str | None = None) -> ProjectRef:
        self.resolve_project_calls.append(selector)
        return resolve_project(self.projects, selector)

    def resolve_repository(self, selector: str, *, project: str | None = None) -> RepositoryRef:
        self.resolve_repository_calls.append(ResolveRepositoryCall(selector, project))
        return resolve_repository(self.repositories, selector, project=project)

    def targets(self, repo: str | None = None, project: str | None = None) -> tuple[RepositoryRef, ...]:
        self.read_targets_calls.append(ReadTargetsCall(repo, project))
        return self._scoped(repo, project)

    def write_targets(
        self,
        repo: str | None,
        project: str | None,
        *,
        all_repos: bool = False,
        all_projects: bool = False,
        operation: str = "mutation",
    ) -> tuple[RepositoryRef, ...]:
        self.write_targets_calls.append(WriteTargetsCall(repo, project, all_repos, all_projects, operation))
        if all_projects:
            return self.repositories
        return self._scoped(repo, project)

    def _scoped(self, repo: str | None, project: str | None) -> tuple[RepositoryRef, ...]:
        scoped = tuple(
            target
            for target in self.repositories
            if project is None or project in {target.project_id, target.project_name}
        )
        if repo is None:
            return scoped
        return (resolve_repository(scoped, repo, project=project),)


@dataclass(frozen=True, slots=True)
class ScheduleWrite:
    repo_id: str
    audit_key: str
    schedule: AuditSchedule


@dataclass(frozen=True, slots=True)
class ImprovementJobWrite:
    repo_id: str
    kind: str
    job: ImprovementJob


@dataclass(frozen=True, slots=True)
class EmailWrite:
    repo_id: str
    audit_key: str
    update: AuditEmailPreferenceUpdate


@dataclass(frozen=True, slots=True)
class SnapshotRead:
    repo_id: str
    audit_key: str
    metric_group: str | None
    task_id: str


class RecordingAuditGateway:
    """Audit upstream that answers from explicit state and records every write.

    Reads are served per repository so a test can prove *which* repository a
    facade fanned out to; writes keep the full argument triple so a swapped
    audit key, improvement kind, or repository id cannot pass unnoticed.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        catalog: AuditCatalogResult = CATALOG,
        schedules: Mapping[str, tuple[AuditSchedule, ...]] | None = None,
        improvement_jobs: Mapping[str, tuple[ImprovementJob, ...]] | None = None,
        email_preferences: Mapping[tuple[str, str], AuditEmailPreference] | None = None,
        reports: Mapping[tuple[str, str], tuple[AuditReportRef, ...]] | None = None,
        artifacts: Mapping[tuple[str, str], AuditArtifact] | None = None,
        task_links: Mapping[str, tuple[AuditTaskLink, ...]] | None = None,
        active_runs: Mapping[str, tuple[AuditRun, ...]] | None = None,
        rerun_state: AuditRerunState | None = None,
        start_failure: Exception | None = None,
    ) -> None:
        self.catalog_result = catalog
        self.schedules = dict(schedules or {})
        self.improvement_jobs = dict(improvement_jobs or {})
        self.email_preferences = dict(email_preferences or {})
        self.reports = dict(reports or {})
        self.artifacts = dict(artifacts or {})
        self.task_links_by_repo = dict(task_links or {})
        self.active_runs_by_repo = dict(active_runs or {})
        self.state = rerun_state or AuditRerunState("head", None, True, None, {})
        self.start_failure = start_failure
        self.catalog_calls = 0
        self.started: list[AuditRunRequest] = []
        self.listed_reports: list[tuple[str, str]] = []
        self.snapshot_reads: list[SnapshotRead] = []
        self.schedule_writes: list[ScheduleWrite] = []
        self.improvement_job_writes: list[ImprovementJobWrite] = []
        self.email_writes: list[EmailWrite] = []
        self.listed_schedules: list[str] = []
        self.listed_improvement_jobs: list[str] = []

    def catalog(self) -> AuditCatalogResult:
        self.catalog_calls += 1
        return self.catalog_result

    def active_runs(self, repo_id: str) -> AuditRunsResult:
        return AuditRunsResult(self.active_runs_by_repo.get(repo_id, ()))

    def rerun_state(self, _repo_id: str) -> AuditRerunState:
        return self.state

    def task_links(self, repo_id: str) -> AuditTaskLinksResult:
        return AuditTaskLinksResult(self.task_links_by_repo.get(repo_id, ()))

    def task_detail(self, task_id: str) -> AuditTaskDetail:
        return AuditTaskDetail(task_id, "running")

    def runbook_metadata(self, runbook_id: str) -> AuditRunbookMetadata:
        return AuditRunbookMetadata(runbook_id, f"Runbook {runbook_id}", "runbook description")

    def start_audit_run(self, request: AuditRunRequest) -> AuditRunResult:
        self.started.append(request)
        if self.start_failure is not None:
            raise self.start_failure
        return AuditRunResult(f"task-{len(self.started)}", "queued")

    def list_audit_reports(self, repo_id: str, metric_group: str) -> tuple[AuditReportRef, ...]:
        self.listed_reports.append((repo_id, metric_group))
        return self.reports.get((repo_id, metric_group), ())

    def read_audit_snapshot(
        self, repo_id: str, audit_key: str, metric_group: str | None = None, *, task_id: str
    ) -> AuditArtifact:
        self.snapshot_reads.append(SnapshotRead(repo_id, audit_key, metric_group, task_id))
        return self.artifacts[repo_id, audit_key]

    def list_schedules(self, repo_id: str) -> tuple[AuditSchedule, ...]:
        self.listed_schedules.append(repo_id)
        return self.schedules.get(repo_id, ())

    def set_schedule(self, repo_id: str, audit_key: str, schedule: AuditSchedule) -> AuditSchedule:
        self.schedule_writes.append(ScheduleWrite(repo_id, audit_key, schedule))
        return schedule

    def get_email_preferences(self, repo_id: str, audit_key: str) -> AuditEmailPreference:
        return self.email_preferences.get((repo_id, audit_key), AuditEmailPreference(audit_key))

    def set_email_preference(
        self, repo_id: str, audit_key: str, update: AuditEmailPreferenceUpdate
    ) -> AuditEmailPreference:
        self.email_writes.append(EmailWrite(repo_id, audit_key, update))
        return AuditEmailPreference(audit_key, update.manual, update.scheduled)

    def list_improvement_jobs(self, repo_id: str) -> tuple[ImprovementJob, ...]:
        self.listed_improvement_jobs.append(repo_id)
        return self.improvement_jobs.get(repo_id, ())

    def set_improvement_job(self, repo_id: str, kind: str, job: ImprovementJob) -> ImprovementJob:
        self.improvement_job_writes.append(ImprovementJobWrite(repo_id, kind, job))
        return job


@dataclass(frozen=True, slots=True)
class AddedRepository:
    project_id: str
    identity: RepositoryIdentity
    repo_access_credential_id: str | None


@dataclass(frozen=True, slots=True)
class MovedRepository:
    source_project_id: str
    repo_id: str
    target_project_id: str


class RecordingPortfolioGateway:
    """Portfolio upstream backed by explicit project details, recording writes.

    ``add`` and ``move`` are the paths where a same-typed argument swap in a
    facade is silent, so both keep the project and repository identifiers they
    were called with, in order.
    """

    def __init__(  # noqa: PLR0913
        self,
        details: Sequence[ProjectDetail] = (),
        *,
        preferences: AccountPreferences = DEFAULT_PREFERENCES,
        access_info: AccessInfo | None = None,
        active_runs: Mapping[str, tuple[PortfolioActiveRun, ...]] | None = None,
        preflight: MovePreflight = ALLOWED_MOVE,
        failure: Exception | None = None,
        added_recon_done: bool | None = True,
    ) -> None:
        self.details = {detail.project.project_id: detail for detail in details}
        self.added_recon_done = added_recon_done
        self.preferences = preferences
        self.access_info = access_info or AccessInfo("free", True, AccessLimits())
        self.active_runs = dict(active_runs or {})
        self.preflight = preflight
        self.failure = failure
        self.created_projects: list[str] = []
        self.renamed_projects: list[tuple[str, str]] = []
        self.deleted_projects: list[str] = []
        self.added_repositories: list[AddedRepository] = []
        self.removed_repositories: list[tuple[str, str]] = []
        self.connected_repositories: list[tuple[str, str]] = []
        self.preflights: list[MovedRepository] = []
        self.moved_repositories: list[MovedRepository] = []
        self.written_preferences: list[AccountPreferences] = []

    def list_projects(self) -> tuple[ProjectRef, ...]:
        if self.failure is not None:
            raise self.failure
        return tuple(detail.project for detail in self.details.values())

    def project_detail(self, project_id: str) -> ProjectDetail:
        if self.failure is not None:
            raise self.failure
        return self.details[project_id]

    def project_active_runs(self, project_id: str) -> tuple[PortfolioActiveRun, ...]:
        return self.active_runs.get(project_id, ())

    def create_project(self, name: str) -> ProjectRef:
        self.created_projects.append(name)
        return ProjectRef(f"p-{len(self.created_projects)}", name)

    def rename_project(self, project_id: str, name: str) -> ProjectRef:
        self.renamed_projects.append((project_id, name))
        return ProjectRef(project_id, name)

    def delete_project(self, project_id: str) -> None:
        self.deleted_projects.append(project_id)

    def add_repository(
        self, project_id: str, identity: RepositoryIdentity, repo_access_credential_id: str | None = None
    ) -> RepositoryRef:
        self.added_repositories.append(AddedRepository(project_id, identity, repo_access_credential_id))
        detail = self.details[project_id]
        added = repository(
            identity.locator,
            repo_id=f"added-{len(self.added_repositories)}",
            project=detail.project,
            host=identity.host,
            provider=identity.provider,
            recon_done=self.added_recon_done,
        )
        # Membership is the point of this call, so the added repository has to
        # be visible to every later read -- the recon continuation resolves it
        # against this same gateway.
        self.details[project_id] = replace(detail, repositories=(*detail.repositories, added))
        return added

    def remove_repository(self, project_id: str, repo_id: str) -> None:
        self.removed_repositories.append((project_id, repo_id))

    def connect_repository(self, project_id: str, repo_id: str) -> RepositoryRef:
        self.connected_repositories.append((project_id, repo_id))
        detail = self.details[project_id]
        existing = next(item for item in detail.repositories if item.repo_id == repo_id)
        return repository(
            existing.identity.locator,
            repo_id=repo_id,
            project=detail.project,
            host=existing.identity.host,
            provider=existing.identity.provider,
            connected=True,
            recon_done=True,
        )

    def preflight_repository_move(self, source_project_id: str, repo_id: str, target_project_id: str) -> MovePreflight:
        self.preflights.append(MovedRepository(source_project_id, repo_id, target_project_id))
        return self.preflight

    def move_repository(self, source_project_id: str, repo_id: str, target_project_id: str) -> RepositoryRef:
        self.moved_repositories.append(MovedRepository(source_project_id, repo_id, target_project_id))
        detail = self.details[target_project_id]
        existing = next(item for item in self.details[source_project_id].repositories if item.repo_id == repo_id)
        return repository(
            existing.identity.locator,
            repo_id=repo_id,
            project=detail.project,
            host=existing.identity.host,
            provider=existing.identity.provider,
        )

    def get_preferences(self) -> AccountPreferences:
        return self.preferences

    def set_preferences(self, preferences: AccountPreferences) -> AccountPreferences:
        self.written_preferences.append(preferences)
        self.preferences = preferences
        return preferences

    def access(self) -> AccessInfo:
        if self.failure is not None:
            raise self.failure
        return self.access_info


class RecordingAuthSession:
    """Credential store seam that records the raw material it was handed."""

    def __init__(self, *, status: AuthSessionStatus | None = None, failure: Exception | None = None) -> None:
        self.imported_cookies: list[str] = []
        self.imported_tokens: list[str] = []
        self.status_calls = 0
        self.failure = failure
        self.session_status = status or AuthSessionStatus(authenticated=True, credential_type="bearer")

    def import_cookie(self, raw_cookie: str) -> ImportCredentialPayload:
        if self.failure is not None:
            raise self.failure
        self.imported_cookies.append(raw_cookie)
        return ImportCredentialPayload(ok=True, auth_file="auth.json", credential_type="cookie")

    def import_bearer_token(self, raw_token: str) -> ImportCredentialPayload:
        if self.failure is not None:
            raise self.failure
        self.imported_tokens.append(raw_token)
        return ImportCredentialPayload(ok=True, auth_file="auth.json", credential_type="bearer")

    def status(self) -> AuthSessionStatus:
        self.status_calls += 1
        if self.failure is not None:
            raise self.failure
        return self.session_status


class RecordingGitLabGateway:
    """GitLab discovery that records the exact typed query it received."""

    def __init__(self, *, projects: GitLabProjectsResult, credentials: GitLabCredentialsResult | None = None) -> None:
        self.projects_result = projects
        self.credentials_result = credentials
        self.queries: list[GitLabProjectsQuery] = []
        self.credential_queries: list[GitLabCredentialsQuery] = []

    def list_credentials(self, query: GitLabCredentialsQuery | None = None) -> GitLabCredentialsResult:
        self.credential_queries.append(query or GitLabCredentialsQuery())
        assert self.credentials_result is not None
        return self.credentials_result

    def discover_projects(self, query: GitLabProjectsQuery) -> GitLabProjectsResult:
        self.queries.append(query)
        return self.projects_result


def recording_application(
    *,
    audit: RecordingAuditGateway | None = None,
    portfolio: RecordingPortfolioGateway | None = None,
    targets: RecordingTargetService | None = None,
    auth: RecordingAuthSession | None = None,
    gitlab: RecordingGitLabGateway | None = None,
) -> Application:
    """Build the real facade tree over recording ports.

    A CLI command reaches several ports even when the test only cares about
    one, so unnamed ports get a recording default rather than an inert stub.
    """
    return ApplicationStubs(
        audit_gateway=audit or RecordingAuditGateway(),
        portfolio_gateway=portfolio or RecordingPortfolioGateway((ProjectDetail(PETS, (REPOSITORY,)),)),
        target_service=targets or RecordingTargetService(),
        auth=auth or RecordingAuthSession(),
        gitlab_gateway=gitlab if gitlab is not None else object(),
    ).build()
