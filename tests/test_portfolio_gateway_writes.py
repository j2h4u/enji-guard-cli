"""The destructive portfolio operations, asserted at the wire boundary.

These are the calls that create, rename, delete and move things inside a
customer's real account, and they were the least covered code in the
gateway.  Each test drives the real :class:`PortfolioGateway` over a fake
transport and asserts the exact method, URL and body it produced, plus
the neutral model it translated the response into -- so a swapped
project/repo path segment or a changed HTTP verb fails here.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from enji_guard_cli.auth_session.adapters import StoredCredentialReader
from enji_guard_cli.auth_session.api import import_bearer_token
from enji_guard_cli.enji_gateway.portfolio_gateway import PortfolioGateway
from enji_guard_cli.portfolio.errors import PortfolioMalformedError
from enji_guard_cli.portfolio.models import (
    AccountPreferences,
    MovePreflight,
    ProjectRef,
    RepositoryIdentity,
    RepositoryIdentitySource,
    RepositoryProvider,
)
from enji_guard_cli.transport import EnjiHttpRequest, EnjiHttpResponse

AUTH_PORT = StoredCredentialReader()

GITHUB_IDENTITY = RepositoryIdentity(RepositoryProvider.GITHUB, "owner/name", "github.com")
GITLAB_IDENTITY = RepositoryIdentity(RepositoryProvider.GITLAB, "group/sub/name", "gitlab.example.com")

GITHUB_REPO_PAYLOAD = {
    "id": "repo_1",
    "provider": "github",
    "host": "github.com",
    "githubOwner": "owner",
    "githubName": "name",
    "webUrl": "https://github.com/owner/name",
}


@dataclass
class FakeEnjiHttpClient:
    """Serve scripted responses and keep every request for assertion."""

    responses: list[EnjiHttpResponse]
    requests: list[EnjiHttpRequest] = field(default_factory=list)

    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def json_response(payload: object, *, status_code: int = 200) -> EnjiHttpResponse:
    return EnjiHttpResponse(
        status_code=status_code, headers={}, content=json.dumps(payload).encode("utf-8"), set_cookie_headers=()
    )


def empty_response() -> EnjiHttpResponse:
    return EnjiHttpResponse(status_code=204, headers={}, content=b"")


@dataclass(frozen=True, slots=True)
class SentRequest:
    method: str
    url: str
    body: object


def sent(client: FakeEnjiHttpClient) -> list[SentRequest]:
    return [SentRequest(request.method, request.url, request.json_body) for request in client.requests]


def gateway(tmp_path: Path, responses: list[EnjiHttpResponse]) -> tuple[PortfolioGateway, FakeEnjiHttpClient]:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(responses)
    return PortfolioGateway(auth_file, client, auth_port=AUTH_PORT), client


def test_create_project_returns_the_created_reference(tmp_path: Path) -> None:
    port, client = gateway(
        tmp_path,
        [
            json_response({"id": "project_1"}, status_code=201),
            json_response({"project": {"id": "project_1", "name": "Pets"}}, status_code=201),
        ],
    )

    assert port.create_project("Pets") == ProjectRef(project_id="project_1", name="Pets")
    assert [(request.method, request.url) for request in sent(client)] == [
        ("POST", "https://fleet.enji.ai/api/v1/projects"),
        ("POST", "https://fleet.enji.ai/api/ux/projects"),
    ]
    assert sent(client)[0].body == {"name": "Pets"}


def test_create_project_reads_a_flat_project_response(tmp_path: Path) -> None:
    """The UX create may answer with the project fields at the top level."""
    port, _client = gateway(
        tmp_path,
        [
            json_response({"id": "project_9"}, status_code=201),
            json_response({"id": "project_9", "name": "Pets"}, status_code=201),
        ],
    )

    assert port.create_project("Pets") == ProjectRef(project_id="project_9", name="Pets")


def test_rename_project_patches_exactly_the_named_project(tmp_path: Path) -> None:
    port, client = gateway(tmp_path, [json_response({"project": {"id": "project_1", "name": "Friends"}})])

    assert port.rename_project("project_1", "Friends") == ProjectRef(project_id="project_1", name="Friends")
    assert sent(client) == [
        SentRequest("PATCH", "https://fleet.enji.ai/api/ux/projects/project_1", {"name": "Friends"})
    ]


def test_rename_project_reports_the_requested_name_when_none_is_echoed(tmp_path: Path) -> None:
    port, _client = gateway(tmp_path, [json_response({})])

    assert port.rename_project("project_1", "Friends") == ProjectRef(project_id="project_1", name="Friends")


def test_delete_project_deletes_both_records_of_the_same_project(tmp_path: Path) -> None:
    port, client = gateway(tmp_path, [empty_response(), empty_response()])

    assert port.delete_project("project_1") is None
    assert [(request.method, request.url) for request in sent(client)] == [
        ("DELETE", "https://fleet.enji.ai/api/ux/projects/project_1"),
        ("DELETE", "https://fleet.enji.ai/api/v1/projects/project_1"),
    ]


def test_add_github_repository_posts_the_owner_and_name_to_that_project(tmp_path: Path) -> None:
    port, client = gateway(tmp_path, [json_response({"repo": GITHUB_REPO_PAYLOAD}, status_code=201)])

    added = port.add_repository("project_1", GITHUB_IDENTITY)

    assert sent(client) == [
        SentRequest(
            "POST",
            "https://fleet.enji.ai/api/ux/projects/project_1/repos",
            {"githubOwner": "owner", "githubName": "name"},
        )
    ]
    assert added.repo_id == "repo_1"
    assert added.project_id == "project_1"
    assert added.identity == GITHUB_IDENTITY
    assert added.identity_source is RepositoryIdentitySource.ENJI
    assert added.provider_repo_id == "repo_1"


def test_add_gitlab_repository_carries_the_host_path_and_credential(tmp_path: Path) -> None:
    port, client = gateway(
        tmp_path,
        [
            json_response(
                {
                    "repository": {
                        "id": "repo_2",
                        "provider": "gitlab",
                        "host": "gitlab.example.com",
                        "repoPath": "group/sub/name",
                        "providerRepoId": "4242",
                        "webUrl": "https://gitlab.example.com/group/sub/name",
                    }
                },
                status_code=201,
            )
        ],
    )

    added = port.add_repository("project_1", GITLAB_IDENTITY, "cred_7")

    assert sent(client) == [
        SentRequest(
            "POST",
            "https://fleet.enji.ai/api/ux/projects/project_1/repos",
            {
                "provider": "gitlab",
                "host": "gitlab.example.com",
                "repoPath": "group/sub/name",
                "repoAccessCredentialId": "cred_7",
            },
        )
    ]
    assert added.identity == GITLAB_IDENTITY
    assert added.provider_repo_id == "4242"
    assert added.identity_source is RepositoryIdentitySource.PROVIDER


def test_a_github_add_refuses_a_repo_access_credential(tmp_path: Path) -> None:
    """The credential is a GitLab-only concept; sending it would be silent."""
    port, client = gateway(tmp_path, [])

    with pytest.raises(ValueError, match="only valid for GitLab"):
        port.add_repository("project_1", GITHUB_IDENTITY, "cred_7")

    assert client.requests == []


def test_a_gitlab_add_requires_a_repo_access_credential(tmp_path: Path) -> None:
    port, client = gateway(tmp_path, [])

    with pytest.raises(ValueError, match="requires an explicit repo access credential"):
        port.add_repository("project_1", GITLAB_IDENTITY)

    assert client.requests == []


def test_remove_repository_deletes_the_repository_inside_its_project(tmp_path: Path) -> None:
    """The two path segments are distinct ids; swapping them deletes elsewhere."""
    port, client = gateway(tmp_path, [empty_response()])

    assert port.remove_repository("project_1", "repo_1") is None
    assert sent(client) == [SentRequest("DELETE", "https://fleet.enji.ai/api/ux/projects/project_1/repos/repo_1", None)]


def test_connect_repository_puts_the_connection_of_that_repository(tmp_path: Path) -> None:
    port, client = gateway(
        tmp_path, [json_response({"repo": {**GITHUB_REPO_PAYLOAD, "connected": True, "reconDone": False}})]
    )

    connected = port.connect_repository("project_1", "repo_1")

    assert [(request.method, request.url) for request in sent(client)] == [
        ("PUT", "https://fleet.enji.ai/api/ux/projects/project_1/repos/repo_1/connection")
    ]
    assert connected.connected is True
    assert connected.recon_done is False


def test_preflight_repository_move_asks_the_source_project_about_the_target(tmp_path: Path) -> None:
    port, client = gateway(tmp_path, [empty_response()])

    assert port.preflight_repository_move("project_1", "repo_1", "project_2") == MovePreflight()
    assert sent(client) == [
        SentRequest(
            "POST",
            "https://fleet.enji.ai/api/ux/projects/project_1/repos/repo_1/transfer/preflight",
            {"targetProjectId": "project_2"},
        )
    ]


def test_move_repository_transfers_from_the_source_and_reports_the_target(tmp_path: Path) -> None:
    """Source and target are same-typed ids; swapping them moves the wrong way."""
    port, client = gateway(tmp_path, [json_response({"repo": GITHUB_REPO_PAYLOAD})])

    moved = port.move_repository("project_1", "repo_1", "project_2")

    assert sent(client) == [
        SentRequest(
            "POST",
            "https://fleet.enji.ai/api/ux/projects/project_1/repos/repo_1/transfer",
            {"targetProjectId": "project_2"},
        )
    ]
    assert moved.repo_id == "repo_1"
    assert moved.project_id == "project_2"


def test_set_preferences_puts_the_language_and_reports_what_stuck(tmp_path: Path) -> None:
    port, client = gateway(tmp_path, [json_response({"preferences": {"language": "ru"}})])

    assert port.set_preferences(AccountPreferences("ru")) == AccountPreferences("ru")
    assert sent(client) == [SentRequest("PUT", "https://fleet.enji.ai/api/ux/user-preferences", {"language": "ru"})]


def test_set_preferences_falls_back_to_the_requested_language(tmp_path: Path) -> None:
    port, _client = gateway(tmp_path, [json_response({})])

    assert port.set_preferences(AccountPreferences("en")) == AccountPreferences("en")


@pytest.mark.parametrize("language", [None, "", "   "])
def test_set_preferences_refuses_an_empty_language_without_calling_upstream(
    tmp_path: Path, language: str | None
) -> None:
    port, client = gateway(tmp_path, [])

    with pytest.raises(ValueError, match="must contain a language"):
        port.set_preferences(AccountPreferences(language))

    assert client.requests == []


def test_a_repository_response_without_an_id_is_rejected(tmp_path: Path) -> None:
    port, _client = gateway(tmp_path, [json_response({"repo": {"provider": "github"}})])

    with pytest.raises(PortfolioMalformedError, match="missing repository id"):
        port.connect_repository("project_1", "repo_1")


@pytest.mark.parametrize(
    "repo",
    [
        {"id": "repo_1", "provider": "hg-hosting", "host": "h", "repoPath": "a/b", "webUrl": "https://h/a/b"},
        {"id": "repo_1", "provider": "github", "githubOwner": "owner", "githubName": "name"},
        {"id": "repo_1", "provider": "gitlab", "host": "h", "webUrl": "https://h/a/b", "providerRepoId": "1"},
    ],
)
def test_a_repository_response_missing_neutral_identity_is_rejected(tmp_path: Path, repo: dict[str, object]) -> None:
    """An unknown provider is dropped, and a dropped field must not be guessed."""
    port, _client = gateway(tmp_path, [json_response({"repo": repo})])

    with pytest.raises(PortfolioMalformedError, match="missing neutral provider identity"):
        port.connect_repository("project_1", "repo_1")


def test_a_repository_response_with_an_unusable_locator_is_rejected(tmp_path: Path) -> None:
    port, _client = gateway(
        tmp_path,
        [
            json_response(
                {
                    "repo": {
                        "id": "repo_1",
                        "provider": "gitlab",
                        "host": "gitlab.example.com:99999",
                        "repoPath": "group/name",
                        "providerRepoId": "1",
                        "webUrl": "https://example.com/group/name",
                    }
                }
            )
        ],
    )

    with pytest.raises(PortfolioMalformedError, match="invalid neutral identity"):
        port.connect_repository("project_1", "repo_1")


def test_list_projects_reads_the_items_key_when_the_response_has_no_projects_key(tmp_path: Path) -> None:
    port, _client = gateway(tmp_path, [json_response({"items": [{"id": "project_1", "name": "Pets"}]})])

    assert port.list_projects() == (ProjectRef(project_id="project_1", name="Pets"),)


def test_list_projects_drops_entries_that_carry_no_project_id(tmp_path: Path) -> None:
    port, _client = gateway(tmp_path, [json_response({"projects": [{"name": "nameless"}, {"projectId": "project_2"}]})])

    assert port.list_projects() == (ProjectRef(project_id="project_2", name=None),)


def test_active_runs_drop_entries_that_name_no_repository(tmp_path: Path) -> None:
    port, _client = gateway(
        tmp_path, [json_response({"activeRuns": [{"state": "running"}, {"repoId": "repo_1", "state": "running"}]})]
    )

    runs = port.project_active_runs("project_1")

    assert [run.repo_id for run in runs] == ["repo_1"]
