"""Neutral product models owned by the Portfolio context.

These models intentionally contain no upstream JSON or Fleet field names.
Adapters translate wire responses before they enter this package.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit

_MAX_PORT = 65535


class RepositoryProvider(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"


class RepositoryIdentitySource(StrEnum):
    """Namespace of the stable identifier carried by a repository reference."""

    PROVIDER = "provider"
    ENJI = "enji"


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    provider: RepositoryProvider
    locator: str
    host: str

    def __post_init__(self) -> None:
        host = self.host.strip().casefold().rstrip("/")
        locator = self.locator.strip()
        if not host or "/" in host or "@" in host or "?" in host or "#" in host:
            raise ValueError("repository host must be a canonical hostname")
        if ":" in host:
            if self.provider is not RepositoryProvider.GITLAB:
                raise ValueError("repository host must be a canonical hostname")
            try:
                parsed_host = urlsplit(f"https://{host}")
                if (
                    parsed_host.hostname is None
                    or ":" in parsed_host.hostname
                    or parsed_host.port is None
                    or not 1 <= parsed_host.port <= _MAX_PORT
                ):
                    raise ValueError
                host = f"{parsed_host.hostname.casefold()}:{parsed_host.port}"
            except ValueError as exc:
                raise ValueError("repository host must be a canonical hostname") from exc
        parts = locator.split("/")
        minimum = 2
        if (
            not locator
            or len(parts) < minimum
            or locator.startswith("/")
            or locator.endswith("/")
            or any(not part for part in parts)
        ):
            raise ValueError("repository locator must be a non-empty provider-native path")
        if self.provider is RepositoryProvider.GITHUB and len(parts) != minimum:
            raise ValueError("GitHub repository locator must contain exactly owner/name")
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "locator", locator)

    @property
    def canonical_locator(self) -> str:
        """Provider-aware lookup key; GitHub names are case-insensitive."""
        return self.locator.casefold() if self.provider is RepositoryProvider.GITHUB else self.locator

    @property
    def canonical_key(self) -> tuple[str, str, str]:
        return self.provider.value, self.host, self.canonical_locator

    def matches(self, other: object) -> bool:
        """Compare the stable provider locator used for idempotent writes."""
        return isinstance(other, RepositoryIdentity) and self.canonical_key == other.canonical_key


@dataclass(frozen=True, slots=True)
class ProjectRef:
    project_id: str
    name: str | None


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    repo_id: str
    project_id: str
    project_name: str | None
    identity: RepositoryIdentity
    web_url: str
    provider_repo_id: str
    connected: bool | None = None
    recon_done: bool | None = None
    scores: Mapping[str, float | int | None] = field(default_factory=dict)
    identity_source: RepositoryIdentitySource = RepositoryIdentitySource.PROVIDER

    @property
    def stable_identity_key(self) -> tuple[str, str, str, str]:
        """Return an explicitly namespaced stable read identity key."""

        return self.identity_source.value, self.identity.provider.value, self.identity.host, self.provider_repo_id


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
