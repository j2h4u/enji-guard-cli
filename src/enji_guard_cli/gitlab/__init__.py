"""GitLab discovery domain types and ports."""

from enji_guard_cli.gitlab.models import (
    GitLabCredential,
    GitLabCredentialPage,
    GitLabCredentialsResult,
    GitLabProject,
    GitLabProjectPage,
    GitLabProjectsResult,
    GitLabScope,
)
from enji_guard_cli.gitlab.ports import GitLabDiscoveryPort

__all__ = [
    "GitLabCredential",
    "GitLabCredentialPage",
    "GitLabCredentialsResult",
    "GitLabDiscoveryPort",
    "GitLabProject",
    "GitLabProjectPage",
    "GitLabProjectsResult",
    "GitLabScope",
]
