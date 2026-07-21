"""Portfolio anti-corruption adapter.

Only this module interprets Enji project/repository vocabulary.  The returned
objects are the neutral Portfolio models and deliberately drop unknown wire
extensions.
"""

from collections.abc import Mapping
from typing import cast

from enji_guard_cli.enji_gateway.http import (
    LanguageCode,
    RepoTransfer,
)
from enji_guard_cli.enji_gateway.http import (
    access as _access,
)
from enji_guard_cli.enji_gateway.http import (
    add_project_repo as _add_project_repo,
)
from enji_guard_cli.enji_gateway.http import (
    connect_project_repo as _connect_project_repo,
)
from enji_guard_cli.enji_gateway.http import (
    create_project as _create_project,
)
from enji_guard_cli.enji_gateway.http import (
    delete_project as _delete_project,
)
from enji_guard_cli.enji_gateway.http import (
    delete_project_repo as _delete_project_repo,
)
from enji_guard_cli.enji_gateway.http import (
    move_repo as _move_repo,
)
from enji_guard_cli.enji_gateway.http import (
    preflight_repo_move as _preflight_repo_move,
)
from enji_guard_cli.enji_gateway.http import (
    project_active_runs as _project_active_runs,
)
from enji_guard_cli.enji_gateway.http import (
    project_detail as _project_detail,
)
from enji_guard_cli.enji_gateway.http import (
    projects as _projects,
)
from enji_guard_cli.enji_gateway.http import (
    put_user_language as _put_user_language,
)
from enji_guard_cli.enji_gateway.http import (
    rename_project as _rename_project,
)
from enji_guard_cli.enji_gateway.http import (
    user_preferences as _user_preferences,
)
from enji_guard_cli.enji_gateway.ports import GatewayAuthFile, GatewayAuthPort, GatewayClient
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue
from enji_guard_cli.portfolio.errors import PortfolioMalformedError
from enji_guard_cli.portfolio.models import (
    AccessInfo,
    AccessLimits,
    AccountPreferences,
    MovePreflight,
    PortfolioActiveRun,
    ProjectDetail,
    ProjectRef,
    RepositoryIdentity,
    RepositoryIdentitySource,
    RepositoryProvider,
    RepositoryRef,
)
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort


class PortfolioGateway(PortfolioGatewayPort):
    def __init__(
        self,
        auth_file: GatewayAuthFile = None,
        client: GatewayClient = None,
        *,
        auth_port: GatewayAuthPort,
    ) -> None:
        self._auth_file = auth_file
        self._client = client
        self._auth_port = auth_port

    def list_projects(self) -> tuple[ProjectRef, ...]:
        payload = _projects(self._auth_file, self._client, auth_port=self._auth_port)
        raw = payload.get("projects")
        if not isinstance(raw, list):
            raw = payload.get("items") if isinstance(payload.get("items"), list) else []
        return tuple(project for item in _object_list(raw) if (project := _project_ref(item)) is not None)

    def project_detail(self, project_id: str) -> ProjectDetail:
        payload = _project_detail(project_id, self._auth_file, self._client, auth_port=self._auth_port)
        project_payload = _object(payload.get("project")) or payload
        project = _project_ref(project_payload) or ProjectRef(project_id=project_id, name=None)
        raw_repos = payload.get("repos")
        repos = tuple(repo for item in _object_list(raw_repos) if (repo := _repository_ref(item, project)) is not None)
        raw_web_resources = payload.get("webResources")
        website_pairs = tuple(
            (url, _string_tuple(resource.get("repoIds")))
            for resource in _object_list(raw_web_resources)
            if (url := _optional_str(resource.get("url"))) is not None
        )
        return ProjectDetail(
            project=project,
            repositories=repos,
            linked_websites=tuple(url for url, _ in website_pairs),
            linked_website_repo_ids=dict(website_pairs),
        )

    def project_active_runs(self, project_id: str) -> tuple[PortfolioActiveRun, ...]:
        payload = _project_active_runs(project_id, self._auth_file, self._client, auth_port=self._auth_port)
        raw_runs = payload.get("activeRuns")
        return tuple(run for item in _object_list(raw_runs) if (run := _portfolio_active_run(item)) is not None)

    def create_project(self, name: str) -> ProjectRef:
        payload = _create_project(name, self._auth_file, self._client, auth_port=self._auth_port)
        project = _project_ref(_object(payload.get("project")) or payload)
        return project or ProjectRef(project_id=_optional_str(payload.get("id")) or "", name=name)

    def rename_project(self, project_id: str, name: str) -> ProjectRef:
        payload = _rename_project(project_id, name, self._auth_file, self._client, auth_port=self._auth_port)
        project = _project_ref(_object(payload.get("project")) or payload)
        return project or ProjectRef(project_id=project_id, name=name)

    def delete_project(self, project_id: str) -> None:
        _delete_project(project_id, self._auth_file, self._client, auth_port=self._auth_port)

    def add_repository(
        self, project_id: str, identity: RepositoryIdentity, repo_access_credential_id: str | None = None
    ) -> RepositoryRef:
        if identity.provider is RepositoryProvider.GITHUB and repo_access_credential_id is not None:
            raise ValueError("repo access credential is only valid for GitLab repository adds")
        if identity.provider is RepositoryProvider.GITLAB and repo_access_credential_id is None:
            raise ValueError("GitLab repository add requires an explicit repo access credential id")
        payload = _add_project_repo(
            project_id,
            identity.provider.value,
            identity.locator,
            host=identity.host,
            repo_access_credential_id=repo_access_credential_id,
            auth_file=self._auth_file,
            client=self._client,
            auth_port=self._auth_port,
        )
        return _repository_ref(
            _object(payload.get("repo")) or _object(payload.get("repository")) or payload, ProjectRef(project_id, None)
        )

    def remove_repository(self, project_id: str, repo_id: str) -> None:
        _delete_project_repo(project_id, repo_id, self._auth_file, self._client, auth_port=self._auth_port)

    def connect_repository(self, project_id: str, repo_id: str) -> RepositoryRef:
        payload = _connect_project_repo(project_id, repo_id, self._auth_file, self._client, auth_port=self._auth_port)
        return _repository_ref(
            _object(payload.get("repo")) or _object(payload.get("repository")) or payload, ProjectRef(project_id, None)
        )

    def preflight_repository_move(self, source_project_id: str, repo_id: str, target_project_id: str) -> MovePreflight:
        _preflight_repo_move(
            source_project_id, repo_id, target_project_id, self._auth_file, self._client, auth_port=self._auth_port
        )
        return MovePreflight()

    def move_repository(self, source_project_id: str, repo_id: str, target_project_id: str) -> RepositoryRef:
        payload = _move_repo(
            RepoTransfer(source_project_id, repo_id, target_project_id),
            self._auth_file,
            self._client,
            auth_port=self._auth_port,
        )
        return _repository_ref(
            _object(payload.get("repo")) or _object(payload.get("repository")) or payload,
            ProjectRef(target_project_id, None),
        )

    def get_preferences(self) -> AccountPreferences:
        payload = _user_preferences(self._auth_file, self._client, auth_port=self._auth_port)
        preferences = _object(payload.get("preferences"))
        language = _optional_str(preferences.get("language")) or _optional_str(payload.get("language"))
        return AccountPreferences(language)

    def set_preferences(self, preferences: AccountPreferences) -> AccountPreferences:
        if not isinstance(preferences.language, str) or not preferences.language.strip():
            raise ValueError("preferences must contain a language")
        payload = _put_user_language(
            cast(LanguageCode, preferences.language), self._auth_file, self._client, auth_port=self._auth_port
        )
        nested = _object(payload.get("preferences"))
        return AccountPreferences(
            _optional_str(nested.get("language")) or _optional_str(payload.get("language")) or preferences.language
        )

    def access(self) -> AccessInfo:
        payload = _access(self._auth_file, self._client, auth_port=self._auth_port)
        limits = payload.get("limits")
        return AccessInfo(
            group=_optional_str(payload.get("group")),
            full_access=_optional_bool(payload.get("full_access")),
            limits=AccessLimits(
                can_add_repo=_optional_bool(limits.get("can_add_repo")),
                can_add_website=_optional_bool(limits.get("can_add_website")),
                can_create_project=_optional_bool(limits.get("can_create_project")),
                can_invite_members=_optional_bool(limits.get("can_invite_members")),
                can_run_one_shot_autofix=_optional_bool(limits.get("can_run_one_shot_autofix")),
                can_run_one_shot_pentest=_optional_bool(limits.get("can_run_one_shot_pentest")),
                can_use_schedules=_optional_bool(limits.get("can_use_schedules")),
                audit_runs=dict(limits.get("audit_runs", {})),
                autofix_runs=dict(limits.get("autofix_runs", {})),
            ),
            usage=tuple(payload.get("usage", [])) if isinstance(payload.get("usage"), list) else (),
        )


def _project_ref(payload: JsonObjectPayload) -> ProjectRef | None:
    project_id = _optional_str(payload.get("id")) or _optional_str(payload.get("projectId"))
    if project_id is None:
        return None
    return ProjectRef(project_id=project_id, name=_optional_str(payload.get("name")))


def _repository_ref(payload: JsonObjectPayload, project: ProjectRef) -> RepositoryRef:
    repo_id = _optional_str(payload.get("id")) or _optional_str(payload.get("repoId"))
    if repo_id is None:
        raise PortfolioMalformedError("repository response is missing repository id")
    raw_provider = _optional_str(payload.get("provider"))
    try:
        provider = RepositoryProvider(raw_provider.casefold()) if raw_provider else None
    except ValueError:
        provider = None
    locator = _optional_str(payload.get("repoPath"))
    host = _optional_str(payload.get("host"))
    provider_repo_id = _optional_str(payload.get("providerRepoId"))
    identity_source = RepositoryIdentitySource.PROVIDER
    web_url = _optional_str(payload.get("webUrl"))
    # GitHub project details expose the provider-native path as separate
    # owner/name fields.  Normalize those wire fields at this boundary so the
    # Portfolio model never needs to know about them.  The live response does
    # not populate providerRepoId; the Enji repository id is the only stable
    # read identifier available there, so use it as the neutral id fallback.
    if provider is RepositoryProvider.GITHUB:
        owner = _optional_str(payload.get("githubOwner"))
        name = _optional_str(payload.get("githubName"))
        if owner is not None and name is not None:
            locator = f"{owner}/{name}"
        if provider_repo_id is None:
            provider_repo_id = repo_id
            identity_source = RepositoryIdentitySource.ENJI
    if provider is None or host is None or locator is None or provider_repo_id is None or web_url is None:
        raise PortfolioMalformedError("repository response is missing neutral provider identity fields")
    scores = payload.get("scores")
    score_map = scores if isinstance(scores, Mapping) else {}
    try:
        identity = RepositoryIdentity(provider, locator, host)
    except ValueError as exc:
        raise PortfolioMalformedError("repository response contains invalid neutral identity") from exc
    return RepositoryRef(
        repo_id=repo_id,
        project_id=_optional_str(payload.get("projectId")) or project.project_id,
        project_name=project.name,
        identity=identity,
        connected=_optional_bool(payload.get("connected")),
        recon_done=_optional_bool(payload.get("reconDone")),
        scores={
            key: value
            for key, value in score_map.items()
            if isinstance(key, str)
            and ((isinstance(value, (int, float)) and not isinstance(value, bool)) or value is None)
        },
        web_url=web_url,
        provider_repo_id=provider_repo_id,
        identity_source=identity_source,
    )


def _portfolio_active_run(payload: JsonObjectPayload) -> PortfolioActiveRun | None:
    repo_id = _optional_str(payload.get("repoId"))
    if repo_id is None:
        return None
    return PortfolioActiveRun(
        repo_id=repo_id,
        task_id=_optional_str(payload.get("fleetTaskId")),
        action_key=_optional_str(payload.get("actionKey")),
        status=_optional_str(payload.get("state")),
        created_at=_optional_str(payload.get("createdAt")),
        started_at=_optional_str(payload.get("startedAt")),
        completed_at=_optional_str(payload.get("completedAt")),
    )


def _object(value: JsonValue | None) -> JsonObjectPayload:
    return value if isinstance(value, dict) else {}


def _object_list(value: JsonValue | None) -> list[JsonObjectPayload]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _optional_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: JsonValue | None) -> bool | None:
    return value if isinstance(value, bool) else None


def _string_tuple(value: JsonValue | None) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


__all__ = ["PortfolioGateway"]
