import json
from pathlib import Path
from typing import cast

import pytest

from enji_guard_cli.auth_session.adapters import AuthSessionAdapter
from enji_guard_cli.auth_session.api import import_bearer_token
from enji_guard_cli.delivery.cli.app import _emit, _json
from enji_guard_cli.enji_gateway import GitLabGateway
from enji_guard_cli.enji_gateway import gitlab_gateway as gateway_module
from enji_guard_cli.enji_gateway.http import gitlab_credentials, gitlab_projects
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue
from enji_guard_cli.portfolio.models import RepositoryIdentity, RepositoryProvider
from enji_guard_cli.portfolio.selectors import parse_repository_selector
from enji_guard_cli.transport import EnjiHttpClient, EnjiHttpRequest, EnjiHttpResponse

AUTH_PORT = AuthSessionAdapter()


def _credential(*, credential_id: str = "cred-1", metadata: JsonObjectPayload | None = None) -> JsonObjectPayload:
    return cast(
        JsonObjectPayload,
        {
            "id": credential_id,
            "name": "GitLab",
            "credential_type": "git",
            "provider": "gitlab",
            "scope_type": "project",
            "scope_owner": "project-1",
            "status": "ready",
            "metadata": metadata
            or {"git_host": "gitlab.example.com", "api_base_url": "https://gitlab.example.com/api/v4"},
        },
    )


def _project(project_id: int = 101, *, next_page: int | None = None) -> JsonObjectPayload:
    return {
        "data": [
            {
                "path_with_namespace": "team/service",
                "provider_project_id": project_id,
                "web_url": "https://gitlab.example.com/team/service",
                "clone_http_url": "https://secret@gitlab.example.com/team/service.git",
                "clone_ssh_url": "git@gitlab.example.com:team/service.git",
            }
        ],
        "meta": {"next_page": next_page},
    }


def _gateway() -> GitLabGateway:
    return GitLabGateway(Path("auth.json"), cast(EnjiHttpClient, object()), AUTH_PORT)


def test_gitlab_gateway_discovers_all_pages_and_returns_safe_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [_project(next_page=2), _project(202)]
    credential_payload: JsonObjectPayload = {"data": [_credential()], "meta": {"limit": 50, "offset": 0, "total": 1}}
    calls: list[dict[str, object]] = []

    def credentials(*args: object, **kwargs: object) -> JsonObjectPayload:
        return credential_payload

    def projects(*args: object, **kwargs: object) -> JsonObjectPayload:
        calls.append(kwargs)
        return pages.pop(0)

    monkeypatch.setattr(gateway_module.http, "gitlab_credentials", credentials)
    monkeypatch.setattr(gateway_module.http, "gitlab_projects", projects)

    result = _gateway().discover_projects(all_pages=True)

    assert [project.provider_project_id for project in result.projects] == ["101", "202"]
    assert result.projects[0].selector.canonical_key == ("gitlab", "gitlab.example.com", "team/service")
    assert "clone_http_url" not in result.projects[0].__dict__ if hasattr(result.projects[0], "__dict__") else True
    assert [call["page"] for call in calls] == [1, 2]


def test_gitlab_gateway_requires_explicit_credential_when_scope_has_many(monkeypatch: pytest.MonkeyPatch) -> None:
    payload: JsonObjectPayload = {
        "data": [_credential(), _credential(credential_id="cred-2")],
        "meta": {"limit": 50, "offset": 0, "total": 2},
    }
    monkeypatch.setattr(gateway_module.http, "gitlab_credentials", lambda *args, **kwargs: payload)
    with pytest.raises(ValueError, match="ambiguous"):
        _gateway().discover_projects()


def test_gitlab_gateway_rejects_pagination_cycle_and_duplicate_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    credentials: JsonObjectPayload = {"data": [_credential()], "meta": {"limit": 50, "offset": 0, "total": 1}}
    monkeypatch.setattr(gateway_module.http, "gitlab_credentials", lambda *args, **kwargs: credentials)
    monkeypatch.setattr(gateway_module.http, "gitlab_projects", lambda *args, **kwargs: _project(next_page=1))
    with pytest.raises(ValueError, match="cycle"):
        _gateway().discover_projects(all_pages=True)

    responses = iter([_project(next_page=2), _project(next_page=None)])
    monkeypatch.setattr(gateway_module.http, "gitlab_projects", lambda *args, **kwargs: next(responses))
    with pytest.raises(ValueError, match="duplicated"):
        _gateway().discover_projects(all_pages=True)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"data": [], "meta": {"limit": 1, "offset": 0, "total": 0}}, "pagination"),
        ({"data": [{"id": "x"}], "meta": {"limit": 50, "offset": 0, "total": 1}}, "credential_type"),
        (
            {
                "data": [_credential(metadata={"api_base_url": "https://user:pass@gitlab.example.com/api/v4"})],
                "meta": {"limit": 50, "offset": 0, "total": 1},
            },
            "unsafe",
        ),
        (
            {
                "data": [_credential(metadata={"git_host": "gitlab.example.com/path"})],
                "meta": {"limit": 50, "offset": 0, "total": 1},
            },
            "unsafe",
        ),
        (
            {
                "data": [_credential(metadata={"git_host": "gitlab.example.com:0"})],
                "meta": {"limit": 50, "offset": 0, "total": 1},
            },
            "unsafe",
        ),
        (
            {
                "data": [_credential(metadata={"git_host": "gitlab.example.com:65536"})],
                "meta": {"limit": 50, "offset": 0, "total": 1},
            },
            "unsafe",
        ),
        (
            {
                "data": [_credential(metadata={"git_host": "gitlab.example.com:eighty"})],
                "meta": {"limit": 50, "offset": 0, "total": 1},
            },
            "unsafe",
        ),
    ],
)
def test_gitlab_gateway_rejects_malformed_credentials(payload: JsonObjectPayload, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        gateway_module._parse_credentials(payload, scope=gateway_module.GitLabScope(), limit=50, offset=0)


def test_gitlab_gateway_rejects_unsafe_project_url() -> None:
    credential_payload: JsonObjectPayload = {"data": [_credential()], "meta": {"limit": 50, "offset": 0, "total": 1}}
    with pytest.raises(ValueError, match="unsafe"):
        gateway_module._parse_projects(
            {
                "data": [
                    {
                        "path_with_namespace": "team/service",
                        "provider_project_id": 1,
                        "web_url": "https://gitlab.example.com/x?token=secret",
                    }
                ],
                "meta": {"next_page": None},
            },
            credential=gateway_module._parse_credentials(
                credential_payload, scope=gateway_module.GitLabScope(), limit=50, offset=0
            ).credentials[0],
            seen_project_ids=set(),
        )


def test_gitlab_http_endpoints_preserve_contract_queries(tmp_path: Path) -> None:
    class Client:
        def __init__(self) -> None:
            self.requests: list[EnjiHttpRequest] = []

        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            self.requests.append(request)
            return EnjiHttpResponse(200, {}, b'{"data":[],"meta":{}}')

    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = Client()
    gitlab_credentials(
        auth_file,
        cast(EnjiHttpClient, client),
        scope_type="project",
        scope_owner="p",
        limit=10,
        offset=20,
        auth_port=AUTH_PORT,
    )
    gitlab_projects(
        auth_file,
        cast(EnjiHttpClient, client),
        credential_id="cred-1",
        host="gitlab.example.com",
        api_base_url="https://gitlab.example.com/api/v4",
        search="service",
        page=2,
        per_page=25,
        scope_type="project",
        scope_owner="p",
        auth_port=AUTH_PORT,
    )
    assert client.requests[0].url.endswith(
        "credential_type=git&provider=gitlab&scope_type=project&scope_owner=p&limit=10&offset=20"
    )
    assert client.requests[1].url.endswith(
        "credential_id=cred-1&host=gitlab.example.com&api_base_url=https%3A%2F%2Fgitlab.example.com%2Fapi%2Fv4&search=service&page=2&per_page=25&scope_type=project&scope_owner=p"
    )


def test_gitlab_credentials_accept_missing_or_null_metadata_with_defaults() -> None:
    missing = _credential()
    missing.pop("metadata")
    null_metadata = _credential()
    null_metadata["metadata"] = None
    for payload in (missing, null_metadata):
        result = gateway_module._parse_credentials(
            {"data": [payload], "meta": {"limit": 50, "offset": 0, "total": 1}},
            scope=gateway_module.GitLabScope(),
            limit=50,
            offset=0,
        )
        assert result.credentials[0].git_host == "gitlab.com"
        assert result.credentials[0].api_base_url == "https://gitlab.com/api/v4"


def test_gitlab_gateway_preserves_self_hosted_port_in_project_selector() -> None:
    payload: JsonObjectPayload = {
        "data": [_credential(metadata={"api_base_url": "https://gitlab.example.com:8443/api/v4"})],
        "meta": {"limit": 50, "offset": 0, "total": 1},
    }
    credential = gateway_module._parse_credentials(
        payload, scope=gateway_module.GitLabScope(), limit=50, offset=0
    ).credentials[0]
    projects, _ = gateway_module._parse_projects(_project(), credential=credential, seen_project_ids=set())
    assert credential.git_host == "gitlab.example.com:8443"
    assert projects[0].selector.matches(
        RepositoryIdentity(RepositoryProvider.GITLAB, "team/service", "gitlab.example.com:8443")
    )
    assert parse_repository_selector("gitlab@gitlab.example.com:8443:team/service").matches(projects[0].selector)


def test_gitlab_gateway_paginates_credentials_for_explicit_and_implicit_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page: list[JsonValue] = [_credential(credential_id=f"cred-{index}") for index in range(50)]
    second_page: list[JsonValue] = [_credential(credential_id="cred-51")]
    calls: list[int] = []

    def credentials(*args: object, **kwargs: object) -> JsonObjectPayload:
        offset = cast(int, kwargs["offset"])
        calls.append(offset)
        return {
            "data": first_page if offset == 0 else second_page,
            "meta": {"limit": 50, "offset": offset, "total": 51},
        }

    monkeypatch.setattr(gateway_module.http, "gitlab_credentials", credentials)
    monkeypatch.setattr(gateway_module.http, "gitlab_projects", lambda *args, **kwargs: _project())
    result = _gateway().discover_projects(credential_id="cred-51")
    assert result.credential.id == "cred-51"
    assert calls == [0, 50]

    calls.clear()
    with pytest.raises(ValueError, match="ambiguous"):
        _gateway().discover_projects()
    assert calls == [0, 50]


def test_gitlab_gateway_rejects_duplicate_credential_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    first_page: list[JsonValue] = [_credential(credential_id=f"cred-{index}") for index in range(50)]
    duplicate_page: list[JsonValue] = [_credential(credential_id="cred-1")]

    def credentials(*args: object, **kwargs: object) -> JsonObjectPayload:
        offset = cast(int, kwargs["offset"])
        return {
            "data": first_page if offset == 0 else duplicate_page,
            "meta": {"limit": 50, "offset": offset, "total": 51},
        }

    monkeypatch.setattr(gateway_module.http, "gitlab_credentials", credentials)
    with pytest.raises(ValueError, match="duplicated"):
        _gateway().discover_projects(credential_id="cred-1")


def test_gitlab_gateway_normalizes_default_scope_and_rejects_personal_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload: JsonObjectPayload = {"data": [_credential()], "meta": {"limit": 50, "offset": 0, "total": 1}}
    calls: list[dict[str, object]] = []

    def credentials(*args: object, **kwargs: object) -> JsonObjectPayload:
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(gateway_module.http, "gitlab_credentials", credentials)
    result = _gateway().list_credentials()
    assert result.scope == gateway_module.GitLabScope("personal", None)
    assert calls[0]["scope_type"] == "personal"
    assert calls[0]["scope_owner"] is None

    calls.clear()
    result = _gateway().list_credentials(scope_type=" ", scope_owner=" ")
    assert result.scope == gateway_module.GitLabScope("personal", None)
    assert calls[0]["scope_type"] == "personal"
    assert calls[0]["scope_owner"] is None

    call_count = len(calls)
    with pytest.raises(ValueError, match="only valid for project"):
        _gateway().list_credentials(scope_type="personal", scope_owner="owner")
    assert len(calls) == call_count


def test_gitlab_http_endpoints_omit_empty_optional_query_values(tmp_path: Path) -> None:
    class Client:
        def __init__(self) -> None:
            self.requests: list[EnjiHttpRequest] = []

        async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
            self.requests.append(request)
            return EnjiHttpResponse(200, {}, b'{"data":[],"meta":{}}')

    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = Client()
    gitlab_credentials(auth_file, cast(EnjiHttpClient, client), scope_type="", scope_owner=" ", auth_port=AUTH_PORT)
    gitlab_projects(
        auth_file,
        cast(EnjiHttpClient, client),
        credential_id="cred-1",
        host="",
        api_base_url="",
        search="",
        scope_type="",
        scope_owner=" ",
        auth_port=AUTH_PORT,
    )
    assert client.requests[0].url.endswith("credential_type=git&provider=gitlab&limit=50&offset=0")
    assert client.requests[1].url.endswith("credential_id=cred-1&page=1&per_page=50")


def test_gitlab_selector_is_pasteable_in_json_and_human_output(capsys: pytest.CaptureFixture[str]) -> None:
    identity = RepositoryIdentity(RepositoryProvider.GITLAB, "team/service", "gitlab.example.com:8443")
    selector = "gitlab@gitlab.example.com:8443:team/service"
    assert cast(dict[str, object], _json({"selector": identity}))["selector"] == selector
    _emit({"selector": identity}, True)
    json_output: dict[str, object] = cast(dict[str, object], json.loads(capsys.readouterr().out))
    _emit({"selector": identity}, False)
    human_output = capsys.readouterr().out
    assert json_output["selector"] == selector
    assert selector in human_output
    assert parse_repository_selector(selector).matches(identity)


@pytest.mark.parametrize(
    "selector",
    [
        "gitlab@gitlab.example.com:0:team/service",
        "gitlab@gitlab.example.com:65536:team/service",
        "gitlab@gitlab.example.com:eighty:team/service",
    ],
)
def test_gitlab_selector_rejects_invalid_host_ports(selector: str) -> None:
    with pytest.raises(ValueError, match="port"):
        parse_repository_selector(selector)


def test_gitlab_selector_preserves_namespace_paths_without_host_port() -> None:
    identity = parse_repository_selector("gitlab@gitlab.com:group/subgroup/service")
    assert identity.host == "gitlab.com"
    assert identity.locator == "group/subgroup/service"
