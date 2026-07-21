"""Explicit mutation scope parsing; no implicit batch writes are allowed."""

from dataclasses import dataclass
from typing import Literal

ScopeKind = Literal["repo", "all_repos", "all_projects"]


@dataclass(frozen=True, slots=True)
class MutationScope:
    kind: ScopeKind
    repo: str | None = None
    project: str | None = None

    @classmethod
    def from_args(
        cls,
        repo: str | None = None,
        project: str | None = None,
        *,
        all_repos: bool = False,
        all_projects: bool = False,
        operation: str = "mutation",
    ) -> MutationScope:
        if all_repos and all_projects:
            raise ValueError(f"{operation}: pass --all-repos or --all-projects, not both")
        if all_projects:
            if repo is not None or project is not None:
                raise ValueError(f"{operation}: --all-projects cannot be combined with REPO or --project")
            return cls("all_projects")
        if all_repos:
            if repo is not None:
                raise ValueError(f"{operation}: REPO cannot be combined with --all-repos")
            if project is None:
                raise ValueError(f"{operation}: --all-repos requires --project")
            return cls("all_repos", project=project)
        if repo is None:
            raise ValueError(f"{operation}: pass REPO, --all-repos with --project, or --all-projects")
        return cls("repo", repo=repo, project=project)

    @property
    def is_batch(self) -> bool:
        return self.kind != "repo"


def validate_write_scope(
    repo: str | None, project: str | None, *, all_repos: bool, all_projects: bool, operation: str
) -> MutationScope:
    return MutationScope.from_args(repo, project, all_repos=all_repos, all_projects=all_projects, operation=operation)
