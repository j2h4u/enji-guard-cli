"""GitLab credential and project discovery."""

from dataclasses import dataclass

from enji_guard_cli.gitlab.models import GitLabCredentialsResult, GitLabProjectsQuery, GitLabProjectsResult
from enji_guard_cli.gitlab.ports import GitLabDiscoveryPort


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
    ) -> GitLabCredentialsResult:
        return self.gateway.list_credentials(scope_type=scope_type, scope_owner=scope_owner, limit=limit, offset=offset)

    def gitlab_projects(self, query: GitLabProjectsQuery) -> GitLabProjectsResult:
        return self.gateway.discover_projects(query)


__all__ = ["GitLabFacade"]
