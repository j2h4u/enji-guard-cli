"""GitLab credential and project discovery.

The facade takes the operator's argv values as primitives and returns
application-owned view DTOs.  Delivery therefore never names a
:mod:`enji_guard_cli.gitlab` type in either direction: a rename inside the
GitLab bounded context stops at the mapping functions below.
"""

from dataclasses import dataclass

from enji_guard_cli.application.views import RepositoryIdentityView, repository_identity_view
from enji_guard_cli.gitlab import (
    GitLabCredential,
    GitLabCredentialsResult,
    GitLabDiscoveryPort,
    GitLabProject,
    GitLabProjectsQuery,
    GitLabProjectsResult,
    GitLabScope,
)


@dataclass(frozen=True, slots=True)
class GitLabScopeView:
    """The credential scope a discovery call was answered within."""

    scope_type: str | None
    scope_owner: str | None


@dataclass(frozen=True, slots=True)
class GitLabCredentialView:
    """One stored GitLab credential as the operator sees it."""

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
class GitLabCredentialPageView:
    """Offset paging over the credential list."""

    limit: int
    offset: int
    total: int


@dataclass(frozen=True, slots=True)
class GitLabCredentialsView:
    """Everything ``guard gitlab credentials`` renders."""

    scope: GitLabScopeView
    credentials: tuple[GitLabCredentialView, ...]
    pagination: GitLabCredentialPageView


@dataclass(frozen=True, slots=True)
class GitLabProjectView:
    """One discovered GitLab project.

    ``selector`` is the application's own identity view, so delivery can name
    the repository this project would become without importing portfolio.
    """

    path_with_namespace: str
    provider_project_id: str
    web_url: str | None
    api_base_url: str
    host: str
    selector: RepositoryIdentityView


@dataclass(frozen=True, slots=True)
class GitLabProjectPageView:
    """Page paging over discovered projects."""

    page: int
    per_page: int
    next_page: int | None


@dataclass(frozen=True, slots=True)
class GitLabProjectsView:
    """Everything ``guard gitlab projects`` renders."""

    scope: GitLabScopeView
    credential: GitLabCredentialView
    projects: tuple[GitLabProjectView, ...]
    pagination: GitLabProjectPageView


def _scope_view(scope: GitLabScope) -> GitLabScopeView:
    return GitLabScopeView(scope_type=scope.scope_type, scope_owner=scope.scope_owner)


def _credential_view(credential: GitLabCredential) -> GitLabCredentialView:
    return GitLabCredentialView(
        id=credential.id,
        name=credential.name,
        credential_type=credential.credential_type,
        provider=credential.provider,
        scope_type=credential.scope_type,
        scope_owner=credential.scope_owner,
        status=credential.status,
        last_error=credential.last_error,
        expires_at=credential.expires_at,
        git_host=credential.git_host,
        api_base_url=credential.api_base_url,
        gitlab_health_reason=credential.gitlab_health_reason,
    )


def _project_view(project: GitLabProject) -> GitLabProjectView:
    return GitLabProjectView(
        path_with_namespace=project.path_with_namespace,
        provider_project_id=project.provider_project_id,
        web_url=project.web_url,
        api_base_url=project.api_base_url,
        host=project.host,
        selector=repository_identity_view(project.selector),
    )


@dataclass(frozen=True, slots=True)
class GitLabFacade:
    """Read-only discovery used before a GitLab repository can be added."""

    gateway: GitLabDiscoveryPort

    def gitlab_credentials(
        self,
        *,
        scope_type: str | None = None,
        scope_owner: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> GitLabCredentialsView:
        result: GitLabCredentialsResult = self.gateway.list_credentials(
            scope_type=scope_type, scope_owner=scope_owner, limit=limit, offset=offset
        )
        return GitLabCredentialsView(
            scope=_scope_view(result.scope),
            credentials=tuple(_credential_view(item) for item in result.credentials),
            pagination=GitLabCredentialPageView(
                limit=result.pagination.limit,
                offset=result.pagination.offset,
                total=result.pagination.total,
            ),
        )

    def gitlab_projects(  # noqa: PLR0913
        self,
        *,
        credential_id: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 50,
        all_pages: bool = False,
        scope_type: str | None = None,
        scope_owner: str | None = None,
    ) -> GitLabProjectsView:
        result: GitLabProjectsResult = self.gateway.discover_projects(
            GitLabProjectsQuery(
                credential_id=credential_id,
                search=search,
                page=page,
                per_page=per_page,
                all_pages=all_pages,
                scope_type=scope_type,
                scope_owner=scope_owner,
            )
        )
        return GitLabProjectsView(
            scope=_scope_view(result.scope),
            credential=_credential_view(result.credential),
            projects=tuple(_project_view(item) for item in result.projects),
            pagination=GitLabProjectPageView(
                page=result.pagination.page,
                per_page=result.pagination.per_page,
                next_page=result.pagination.next_page,
            ),
        )


__all__ = [
    "GitLabCredentialPageView",
    "GitLabCredentialView",
    "GitLabCredentialsView",
    "GitLabFacade",
    "GitLabProjectPageView",
    "GitLabProjectView",
    "GitLabProjectsView",
    "GitLabScopeView",
]
