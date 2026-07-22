"""Anti-corruption adapter for Enji's GitLab discovery endpoints."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from enji_guard_cli.enji_gateway import http
from enji_guard_cli.enji_gateway.ports import GatewayAuthPort
from enji_guard_cli.gitlab.models import (
    GitLabCredential,
    GitLabCredentialPage,
    GitLabCredentialsResult,
    GitLabProject,
    GitLabProjectPage,
    GitLabProjectsQuery,
    GitLabProjectsResult,
    GitLabScope,
)
from enji_guard_cli.json_types import JsonObjectPayload
from enji_guard_cli.portfolio.models import RepositoryIdentity, RepositoryProvider
from enji_guard_cli.transport import EnjiHttpClient

_MAX_PORT = 65535


@dataclass(slots=True)
class GitLabGateway:
    """Translate only the GitLab discovery wire contracts into domain DTOs."""

    auth_file: Path | None = None
    client: EnjiHttpClient | None = None
    auth_port: GatewayAuthPort | None = None

    def _auth_port(self) -> GatewayAuthPort:
        if self.auth_port is None:
            raise RuntimeError("GitLab gateway auth port is not configured")
        return self.auth_port

    def list_credentials(
        self,
        *,
        scope_type: str | None = None,
        scope_owner: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> GitLabCredentialsResult:
        if limit <= 0 or offset < 0:
            raise ValueError("credential limit must be positive and offset must be non-negative")
        scope_type, scope_owner = _normalize_scope(scope_type, scope_owner)
        payload = http.gitlab_credentials(
            self.auth_file,
            self.client,
            scope_type=scope_type,
            scope_owner=scope_owner,
            limit=limit,
            offset=offset,
            auth_port=self._auth_port(),
        )
        return _parse_credentials(payload, scope=GitLabScope(scope_type, scope_owner), limit=limit, offset=offset)

    def discover_projects(self, query: GitLabProjectsQuery) -> GitLabProjectsResult:
        if query.page <= 0 or query.per_page <= 0:
            raise ValueError("project page and per-page values must be positive")
        scope_type, scope_owner = _normalize_scope(query.scope_type, query.scope_owner)
        credential = self._resolve_credential(
            credential_id=query.credential_id,
            scope_type=scope_type,
            scope_owner=scope_owner,
        )
        projects: list[GitLabProject] = []
        seen_project_ids: set[str] = set()
        seen_pages: set[int] = set()
        current_page = query.page
        next_page: int | None = query.page

        while next_page is not None:
            if next_page in seen_pages:
                raise ValueError("GitLab project pagination cycle detected")
            seen_pages.add(next_page)
            payload = http.gitlab_projects(
                self.auth_file,
                self.client,
                credential_id=credential.id,
                host=credential.git_host,
                api_base_url=credential.api_base_url,
                search=query.search,
                page=next_page,
                per_page=query.per_page,
                scope_type=scope_type,
                scope_owner=scope_owner,
                auth_port=self._auth_port(),
            )
            parsed_projects, returned_next = _parse_projects(
                payload,
                credential=credential,
                seen_project_ids=seen_project_ids,
            )
            projects.extend(parsed_projects)
            if not query.all_pages:
                next_page = returned_next
                break
            if returned_next is not None and returned_next in seen_pages:
                raise ValueError("GitLab project pagination cycle detected")
            next_page = returned_next
            if next_page is not None and next_page <= current_page:
                raise ValueError("GitLab project pagination moved backwards")
            if next_page is not None:
                current_page = next_page

        pagination = GitLabProjectPage(page=query.page, per_page=query.per_page, next_page=next_page)
        return GitLabProjectsResult(
            scope=GitLabScope(scope_type, scope_owner),
            credential=credential,
            projects=tuple(projects),
            pagination=pagination,
        )

    def _resolve_credential(
        self,
        *,
        credential_id: str | None,
        scope_type: str | None,
        scope_owner: str | None,
    ) -> GitLabCredential:
        requested_id = _optional_str(credential_id)
        first = self.list_credentials(scope_type=scope_type, scope_owner=scope_owner, limit=50, offset=0)
        expected_total = first.pagination.total
        expected_limit = first.pagination.limit
        credentials = list(first.credentials)
        seen_ids = {item.id for item in credentials}
        _validate_credential_page(first, expected_total=expected_total, expected_limit=expected_limit)
        seen_offsets = {first.pagination.offset}
        offset = first.pagination.offset + expected_limit
        while offset < expected_total:
            if offset in seen_offsets:
                raise ValueError("GitLab credential pagination repeated an offset")
            seen_offsets.add(offset)
            current = self.list_credentials(
                scope_type=scope_type,
                scope_owner=scope_owner,
                limit=expected_limit,
                offset=offset,
            )
            if current.pagination.total != expected_total:
                raise ValueError("GitLab credential pagination has inconsistent total")
            _validate_credential_page(current, expected_total=expected_total, expected_limit=expected_limit)
            for item in current.credentials:
                if item.id in seen_ids:
                    raise ValueError(f"GitLab credential id is duplicated: {item.id}")
                seen_ids.add(item.id)
                credentials.append(item)
            offset += expected_limit
        return _select_credential(tuple(credentials), requested_id)


def _select_credential(credentials: tuple[GitLabCredential, ...], credential_id: str | None) -> GitLabCredential:
    if credential_id is not None:
        matches = tuple(item for item in credentials if item.id == credential_id)
        if not matches:
            raise ValueError(f"GitLab credential not found: {credential_id}")
        if len(matches) != 1:
            raise ValueError(f"GitLab credential id is duplicated: {credential_id}")
        return matches[0]
    if len(credentials) != 1:
        raise ValueError("GitLab credential selection is ambiguous; pass --credential-id")
    return credentials[0]


def _validate_credential_page(page: GitLabCredentialsResult, *, expected_total: int, expected_limit: int) -> None:
    count = len(page.credentials)
    if page.pagination.limit != expected_limit or page.pagination.offset + count > expected_total:
        raise ValueError("GitLab credential pagination is inconsistent")
    if page.pagination.offset + count < expected_total and count < expected_limit:
        raise ValueError("GitLab credential pagination is inconsistent")


def _normalize_scope(scope_type: str | None, scope_owner: str | None) -> tuple[str, str | None]:
    normalized_type = _optional_str(scope_type) or "personal"
    normalized_owner = _optional_str(scope_owner)
    if normalized_type is not None and normalized_type not in {"personal", "project"}:
        raise ValueError("GitLab scope type must be personal or project")
    if normalized_type == "personal" and normalized_owner is not None:
        raise ValueError("GitLab scope owner is only valid for project scope")
    if normalized_type == "personal":
        normalized_owner = None
    return normalized_type, normalized_owner


def _parse_credentials(
    payload: JsonObjectPayload, *, scope: GitLabScope, limit: int, offset: int
) -> GitLabCredentialsResult:
    data = _required_list(payload, "data")
    meta = _required_dict(payload, "meta")
    response_limit = _required_int(meta, "limit")
    response_offset = _required_int(meta, "offset")
    total = _required_int(meta, "total")
    if response_limit != limit or response_offset != offset or total < 0:
        raise ValueError("GitLab credentials response has invalid pagination metadata")
    credentials: list[GitLabCredential] = []
    seen_ids: set[str] = set()
    for item in data:
        credential = _parse_credential(item)
        if credential.id in seen_ids:
            raise ValueError(f"GitLab credential id is duplicated: {credential.id}")
        seen_ids.add(credential.id)
        credentials.append(credential)
    return GitLabCredentialsResult(
        scope=scope, credentials=tuple(credentials), pagination=GitLabCredentialPage(limit, offset, total)
    )


def _parse_credential(value: object) -> GitLabCredential:
    item = _dict(value, "credential")
    raw_metadata = item.get("metadata")
    metadata = {} if raw_metadata is None else _dict(raw_metadata, "credential metadata")
    credential_type = _required_str(item, "credential_type")
    provider = _required_str(item, "provider")
    if credential_type != "git" or provider != "gitlab":
        raise ValueError("credential is not a GitLab git credential")
    git_host = _optional_host(metadata.get("git_host"))
    api_base_url = _optional_url(metadata.get("api_base_url"), "api_base_url")
    if git_host is None and api_base_url is not None:
        git_host = _url_host(api_base_url)
    if api_base_url is None and git_host is not None:
        api_base_url = f"https://{git_host}/api/v4"
    if git_host is None and api_base_url is None:
        git_host = "gitlab.com"
        api_base_url = "https://gitlab.com/api/v4"
    credential_scope_type, credential_scope_owner = _normalize_scope(
        _optional_str(item.get("scope_type")),
        _optional_str(item.get("scope_owner")),
    )
    return GitLabCredential(
        id=_required_str(item, "id"),
        name=_required_str(item, "name"),
        credential_type=credential_type,
        provider=provider,
        scope_type=credential_scope_type,
        scope_owner=credential_scope_owner,
        status=_required_str(item, "status"),
        last_error=_optional_str(item.get("last_error")),
        expires_at=_optional_str(item.get("expires_at")),
        git_host=git_host,
        api_base_url=api_base_url,
        gitlab_health_reason=_optional_str(metadata.get("gitlab_health_reason")),
    )


def _parse_projects(
    payload: JsonObjectPayload,
    *,
    credential: GitLabCredential,
    seen_project_ids: set[str],
) -> tuple[tuple[GitLabProject, ...], int | None]:
    data = _required_list(payload, "data")
    meta = _required_dict(payload, "meta")
    raw_next = meta.get("next_page")
    if raw_next is not None and (isinstance(raw_next, bool) or not isinstance(raw_next, int) or raw_next <= 0):
        raise ValueError("GitLab projects response has invalid next_page")
    projects: list[GitLabProject] = []
    for value in data:
        item = _dict(value, "project")
        provider_id = _required_scalar_id(item.get("provider_project_id"), "provider_project_id")
        if provider_id in seen_project_ids:
            raise ValueError(f"GitLab provider project id is duplicated: {provider_id}")
        seen_project_ids.add(provider_id)
        path = _required_str(item, "path_with_namespace")
        api_base_url = _optional_url(item.get("api_base_url"), "api_base_url") or credential.api_base_url
        host = _url_host(api_base_url) if api_base_url is not None else credential.git_host
        if api_base_url is None and host is not None:
            api_base_url = f"https://{host}/api/v4"
        if host is None or api_base_url is None:
            raise ValueError("GitLab project has no safe API base URL or host")
        web_url = _optional_url(item.get("web_url"), "web_url")
        projects.append(
            GitLabProject(
                path_with_namespace=path,
                provider_project_id=provider_id,
                web_url=web_url,
                api_base_url=api_base_url,
                host=host,
                selector=RepositoryIdentity(RepositoryProvider.GITLAB, path, host),
            )
        )
    return tuple(projects), cast(int | None, raw_next)


def _dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"GitLab {label} must be an object")
    return value


def _required_dict(payload: Mapping[str, object], key: str) -> dict[str, object]:
    return _dict(payload.get(key), key)


def _required_list(payload: Mapping[str, object], key: str) -> list[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"GitLab response field {key} must be an array")
    return value


def _required_str(item: Mapping[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"GitLab response field {key} must be a non-empty string")
    return value.strip()


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("GitLab optional string field is malformed")
    return value.strip() or None


def _required_int(item: Mapping[str, object], key: str) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"GitLab response field {key} must be an integer")
    return value


def _required_scalar_id(value: object, key: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"GitLab response field {key} must be a string or integer")
    result = str(value).strip()
    if not result:
        raise ValueError(f"GitLab response field {key} must be non-empty")
    return result


def _optional_host(value: object) -> str | None:
    host = _optional_str(value)
    if host is None:
        return None
    if "://" in host or "/" in host or "@" in host or "?" in host or "#" in host:
        raise ValueError("GitLab host is unsafe")
    try:
        parsed = urlsplit(f"https://{host}")
        if parsed.hostname is None or ":" in parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        port = parsed.port
        if ":" in host and port is None:
            raise ValueError
        if port is not None and not 1 <= port <= _MAX_PORT:
            raise ValueError
    except ValueError as exc:
        raise ValueError("GitLab host is unsafe") from exc
    return f"{parsed.hostname.casefold()}:{port}" if port is not None else parsed.hostname.casefold()


def _optional_url(value: object, field: str) -> str | None:
    url = _optional_str(value)
    if url is None:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError
        if parsed.hostname is None:
            raise ValueError
        if ":" in parsed.hostname:
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= _MAX_PORT:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"GitLab {field} is unsafe") from exc
    return url.rstrip("/")


def _url_host(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.hostname is None or ":" in parsed.hostname:
        raise ValueError("GitLab URL has no hostname")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("GitLab URL has an invalid port") from exc
    if port is not None and not 1 <= port <= _MAX_PORT:
        raise ValueError("GitLab URL has an invalid port")
    return f"{parsed.hostname.casefold()}:{port}" if port is not None else parsed.hostname.casefold()
