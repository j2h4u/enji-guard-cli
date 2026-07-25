"""Application-owned presentation of projects, repositories and status.

Like :mod:`enji_guard_cli.application.audit_views`, these are new types mapped
from :mod:`enji_guard_cli.portfolio` objects -- never aliases or re-exports of
them.  Field names match the domain because they are the operator-visible
``--json`` contract.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from enji_guard_cli.application.audit_views import AuditRunView, AuditStatusView, run_view, status_view
from enji_guard_cli.application.views import RepositoryIdentityView, repository_identity_view
from enji_guard_cli.portfolio.models import (
    AccountPreferences,
    PortfolioActiveRun,
    ProjectRef,
    ProjectSettings,
    RepositoryRef,
)
from enji_guard_cli.portfolio.ports import PortfolioAuditStatus
from enji_guard_cli.portfolio.status import (
    PortfolioOverview,
    ProjectOverview,
    RepositoryOverview,
    RepositoryStatus,
)


@dataclass(frozen=True, slots=True)
class ProjectRefView:
    """How the operator names one project."""

    project_id: str
    name: str | None


@dataclass(frozen=True, slots=True)
class RepositoryRefView:
    """One connected repository and the scores last recorded for it."""

    repo_id: str
    project_id: str
    project_name: str | None
    identity: RepositoryIdentityView
    web_url: str
    provider_repo_id: str
    connected: bool | None
    recon_done: bool | None
    scores: Mapping[str, float | int | None]
    identity_source: str

    @property
    def selector(self) -> str:
        """The ``provider@host:locator`` selector operators type and read."""
        return self.identity.selector


@dataclass(frozen=True, slots=True)
class PortfolioActiveRunView:
    """Work in flight for one repository, as the overview reports it."""

    repo_id: str
    task_id: str | None
    action_key: str | None
    status: str | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class RepositoryOverviewView:
    """One repository's line in the portfolio overview."""

    repository: RepositoryRefView
    active_runs: tuple[PortfolioActiveRunView, ...]


@dataclass(frozen=True, slots=True)
class ProjectOverviewView:
    """One project's block in the portfolio overview."""

    project: ProjectRefView
    repositories: tuple[RepositoryOverviewView, ...]


@dataclass(frozen=True, slots=True)
class PortfolioOverviewView:
    """Everything ``guard status`` renders without a repository argument."""

    observed_at: str
    projects: tuple[ProjectOverviewView, ...]


@dataclass(frozen=True, slots=True)
class PortfolioAuditStatusView:
    """Audit status for one repository, plus the runs still in flight."""

    summary: AuditStatusView
    active_runs: tuple[AuditRunView, ...]


@dataclass(frozen=True, slots=True)
class RepositoryStatusView:
    """Everything ``guard status REPO`` renders for one repository."""

    repository: RepositoryRefView
    audit: PortfolioAuditStatusView


@dataclass(frozen=True, slots=True)
class AccountPreferencesView:
    """Account-wide preferences; language is not a project setting."""

    language: str | None


@dataclass(frozen=True, slots=True)
class ProjectSettingsView:
    """Everything ``guard project settings`` renders."""

    project: ProjectRefView
    repositories: tuple[RepositoryRefView, ...]
    account_preferences: AccountPreferencesView


def project_ref_view(project: ProjectRef) -> ProjectRefView:
    return ProjectRefView(project_id=project.project_id, name=project.name)


def repository_ref_view(repository: RepositoryRef) -> RepositoryRefView:
    return RepositoryRefView(
        repo_id=repository.repo_id,
        project_id=repository.project_id,
        project_name=repository.project_name,
        identity=repository_identity_view(repository.identity),
        web_url=repository.web_url,
        provider_repo_id=repository.provider_repo_id,
        connected=repository.connected,
        recon_done=repository.recon_done,
        scores=repository.scores,
        identity_source=repository.identity_source.value,
    )


def _active_run_view(run: PortfolioActiveRun) -> PortfolioActiveRunView:
    return PortfolioActiveRunView(
        repo_id=run.repo_id,
        task_id=run.task_id,
        action_key=run.action_key,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _repository_overview_view(overview: RepositoryOverview) -> RepositoryOverviewView:
    return RepositoryOverviewView(
        repository=repository_ref_view(overview.repository),
        active_runs=tuple(_active_run_view(run) for run in overview.active_runs),
    )


def _project_overview_view(overview: ProjectOverview) -> ProjectOverviewView:
    return ProjectOverviewView(
        project=project_ref_view(overview.project),
        repositories=tuple(_repository_overview_view(item) for item in overview.repositories),
    )


def overview_view(overview: PortfolioOverview) -> PortfolioOverviewView:
    return PortfolioOverviewView(
        observed_at=overview.observed_at,
        projects=tuple(_project_overview_view(item) for item in overview.projects),
    )


def _audit_status_view(status: PortfolioAuditStatus) -> PortfolioAuditStatusView:
    return PortfolioAuditStatusView(
        summary=status_view(status.summary),
        active_runs=tuple(run_view(run) for run in status.active_runs),
    )


def repository_status_view(status: RepositoryStatus) -> RepositoryStatusView:
    return RepositoryStatusView(
        repository=repository_ref_view(status.repository),
        audit=_audit_status_view(status.audit),
    )


def account_preferences_view(preferences: AccountPreferences) -> AccountPreferencesView:
    return AccountPreferencesView(language=preferences.language)


def project_settings_view(settings: ProjectSettings) -> ProjectSettingsView:
    return ProjectSettingsView(
        project=project_ref_view(settings.project),
        repositories=tuple(repository_ref_view(item) for item in settings.repositories),
        account_preferences=account_preferences_view(settings.account_preferences),
    )


__all__ = [
    "AccountPreferencesView",
    "PortfolioActiveRunView",
    "PortfolioAuditStatusView",
    "PortfolioOverviewView",
    "ProjectOverviewView",
    "ProjectRefView",
    "ProjectSettingsView",
    "RepositoryOverviewView",
    "RepositoryRefView",
    "RepositoryStatusView",
    "account_preferences_view",
    "overview_view",
    "project_ref_view",
    "project_settings_view",
    "repository_ref_view",
    "repository_status_view",
]
