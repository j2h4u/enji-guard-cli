"""Neutral product models owned by the Portfolio context.

These models intentionally contain no upstream JSON or Fleet field names.
Adapters translate wire responses before they enter this package.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ProjectRef:
    project_id: str
    name: str | None


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    repo_id: str
    project_id: str
    project_name: str | None
    full_name: str | None
    connected: bool | None = None
    recon_done: bool | None = None
    scores: Mapping[str, float | int | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectDetail:
    project: ProjectRef
    repositories: tuple[RepositoryRef, ...] = ()
    linked_websites: tuple[str, ...] = ()
    linked_website_repo_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PortfolioActiveRun:
    """Project-level active work projected into Portfolio language."""

    repo_id: str
    task_id: str | None = None
    action_key: str | None = None
    status: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


@dataclass(frozen=True, slots=True)
class AccountPreferences:
    """Account-wide preferences; language is not a project setting."""

    language: str | None = None


@dataclass(frozen=True, slots=True)
class AccessLimits:
    can_add_repo: bool | None = None
    can_add_website: bool | None = None
    can_create_project: bool | None = None
    can_invite_members: bool | None = None
    can_run_one_shot_autofix: bool | None = None
    can_run_one_shot_pentest: bool | None = None
    can_use_schedules: bool | None = None
    audit_runs: Mapping[str, object] = field(default_factory=dict)
    autofix_runs: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AccessInfo:
    group: str | None
    full_access: bool | None
    limits: AccessLimits
    usage: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    """Project view with account preferences represented once."""

    project: ProjectRef
    repositories: tuple[RepositoryRef, ...]
    account_preferences: AccountPreferences


@dataclass(frozen=True, slots=True)
class MovePreflight:
    allowed: bool = True
    schedule_replacements: tuple[str, ...] = ()
    message: str | None = None


@dataclass(frozen=True, slots=True)
class OperationResult:
    state: str
    project: ProjectRef | None = None
    repository: RepositoryRef | None = None
    source_project_id: str | None = None
    target_project_id: str | None = None
    message: str | None = None
    recon: object | None = None
