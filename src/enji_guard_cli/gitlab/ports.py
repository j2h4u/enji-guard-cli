"""Narrow application port for GitLab discovery."""

from typing import Protocol

from enji_guard_cli.gitlab.models import GitLabCredentialsResult, GitLabProjectsResult


class GitLabDiscoveryPort(Protocol):
    def list_credentials(
        self,
        *,
        scope_type: str | None = None,
        scope_owner: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> GitLabCredentialsResult: ...

    def discover_projects(  # noqa: PLR0913
        self,
        *,
        credential_id: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 50,
        all_pages: bool = False,
        scope_type: str | None = None,
        scope_owner: str | None = None,
    ) -> GitLabProjectsResult: ...
