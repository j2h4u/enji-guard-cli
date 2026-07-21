"""Provider-neutral DTOs for the read-only GitLab discovery use-cases."""

from dataclasses import dataclass

from enji_guard_cli.portfolio.models import RepositoryIdentity


@dataclass(frozen=True, slots=True)
class GitLabScope:
    scope_type: str | None = None
    scope_owner: str | None = None


@dataclass(frozen=True, slots=True)
class GitLabCredential:
    id: str
    name: str
    credential_type: str
    provider: str
    scope_type: str | None
    scope_owner: str | None
    status: str
    last_error: str | None
    expires_at: str | None
    git_host: str | None
    api_base_url: str | None
    gitlab_health_reason: str | None


@dataclass(frozen=True, slots=True)
class GitLabCredentialPage:
    limit: int
    offset: int
    total: int


@dataclass(frozen=True, slots=True)
class GitLabCredentialsResult:
    scope: GitLabScope
    credentials: tuple[GitLabCredential, ...]
    pagination: GitLabCredentialPage


@dataclass(frozen=True, slots=True)
class GitLabProject:
    path_with_namespace: str
    provider_project_id: str
    web_url: str | None
    api_base_url: str
    host: str
    selector: RepositoryIdentity


@dataclass(frozen=True, slots=True)
class GitLabProjectPage:
    page: int
    per_page: int
    next_page: int | None


@dataclass(frozen=True, slots=True)
class GitLabProjectsResult:
    scope: GitLabScope
    credential: GitLabCredential
    projects: tuple[GitLabProject, ...]
    pagination: GitLabProjectPage
