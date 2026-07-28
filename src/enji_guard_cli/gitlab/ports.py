"""Narrow application port for GitLab discovery."""

from typing import Protocol

from enji_guard_cli.gitlab.models import (
    GitLabCredentialsQuery,
    GitLabCredentialsResult,
    GitLabProjectsQuery,
    GitLabProjectsResult,
)


class GitLabDiscoveryPort(Protocol):
    def list_credentials(self, query: GitLabCredentialsQuery | None = None) -> GitLabCredentialsResult: ...

    def discover_projects(self, query: GitLabProjectsQuery) -> GitLabProjectsResult: ...
