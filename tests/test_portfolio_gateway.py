from typing import cast

import pytest

from enji_guard_cli.application import Application
from enji_guard_cli.audit.ports import AuditGatewayPort
from enji_guard_cli.auth_session.adapters import AuthSessionAdapter
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.enji_gateway import PortfolioGateway
from enji_guard_cli.enji_gateway.ports import GatewayClient
from enji_guard_cli.json_types import JsonObjectPayload
from enji_guard_cli.portfolio.errors import PortfolioMalformedError
from enji_guard_cli.portfolio.models import RepositoryIdentity, RepositoryProvider


class _AuditGateway:
    pass


class _Auth:
    pass


def test_project_detail_composes_live_collections_into_audit_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload: JsonObjectPayload = {
        "project": {"id": "project-1", "name": "Pets"},
        "repos": [
            {
                "id": "repo-1",
                "projectId": "project-1",
                "provider": "github",
                "host": "github.com",
                "repoPath": "acme/cat",
                "providerRepoId": "provider-1",
                "webUrl": "https://github.com/acme/cat",
                "connected": True,
            },
        ],
        "webResources": [{"id": "site-1", "url": "https://pets.example", "repoIds": ["repo-1"]}],
    }
    import enji_guard_cli.enji_gateway.portfolio_gateway as module

    monkeypatch.setattr(module, "_project_detail", lambda *_args, **_kwargs: payload)
    gateway = PortfolioGateway(client=cast(GatewayClient, object()), auth_port=AuthSessionAdapter())
    application = Application(
        audit_gateway=cast(AuditGatewayPort, _AuditGateway()),
        portfolio_gateway=gateway,
        auth=cast(AuthSessionService, _Auth()),
    )

    project = application._audit_project("project-1")

    assert project.project_id == "project-1"
    assert project.repositories[0].repo_id == "repo-1"
    assert project.repositories[0].locator == "acme/cat"
    assert project.linked_websites[0].url == "https://pets.example"
    assert project.linked_websites[0].repo_ids == ("repo-1",)


def test_project_detail_normalizes_github_wire_identity_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload: JsonObjectPayload = {
        "project": {"id": "project-1", "name": "Pets"},
        "repos": [
            {
                "id": "repo-github-1",
                "projectId": "project-1",
                "provider": "github",
                "host": "github.com",
                "githubOwner": "acme",
                "githubName": "cat",
                "providerRepoId": None,
                "webUrl": "https://github.com/acme/cat",
            },
        ],
    }
    import enji_guard_cli.enji_gateway.portfolio_gateway as module

    monkeypatch.setattr(module, "_project_detail", lambda *_args, **_kwargs: payload)
    gateway = PortfolioGateway(client=cast(GatewayClient, object()), auth_port=AuthSessionAdapter())

    project = gateway.project_detail("project-1")
    repository = project.repositories[0]

    assert repository.identity.provider is RepositoryProvider.GITHUB
    assert repository.identity.host == "github.com"
    assert repository.identity.locator == "acme/cat"
    assert repository.web_url == "https://github.com/acme/cat"
    assert repository.provider_repo_id == "repo-github-1"


def test_project_active_runs_are_project_owned_neutral_models(monkeypatch: pytest.MonkeyPatch) -> None:
    payload: JsonObjectPayload = {
        "activeRuns": [
            {
                "repoId": "repo-1",
                "fleetTaskId": "task-1",
                "actionKey": "audit.security",
                "state": "running",
                "startedAt": "2026-07-17T08:00:00Z",
            }
        ]
    }
    import enji_guard_cli.enji_gateway.portfolio_gateway as module

    monkeypatch.setattr(module, "_project_active_runs", lambda *_args, **_kwargs: payload)

    runs = PortfolioGateway(client=cast(GatewayClient, object()), auth_port=AuthSessionAdapter()).project_active_runs(
        "project-1"
    )

    assert runs[0].repo_id == "repo-1"
    assert runs[0].task_id == "task-1"
    assert runs[0].status == "running"


@pytest.mark.parametrize(
    ("provider", "locator", "host", "credential"),
    [
        (RepositoryProvider.GITHUB, "acme/cat", "github.com", None),
        (RepositoryProvider.GITLAB, "group/subgroup/cat", "gitlab.example", "cred-1"),
    ],
)
def test_add_repository_passes_neutral_identity_and_gitlab_credential(
    monkeypatch: pytest.MonkeyPatch,
    provider: RepositoryProvider,
    locator: str,
    host: str,
    credential: str | None,
) -> None:
    import enji_guard_cli.enji_gateway.portfolio_gateway as module

    captured: dict[str, object] = {}

    def fake_add(*args: object, **kwargs: object) -> JsonObjectPayload:
        captured["args"] = args
        captured.update(kwargs)
        return {
            "id": "repo-1",
            "projectId": "project-1",
            "provider": provider.value,
            "host": host,
            "repoPath": locator,
            "providerRepoId": "provider-1",
            "webUrl": f"https://{host}/{locator}",
        }

    monkeypatch.setattr(module, "_add_project_repo", fake_add)
    identity = RepositoryIdentity(provider, locator, host)
    repo = PortfolioGateway(client=cast(GatewayClient, object()), auth_port=AuthSessionAdapter()).add_repository(
        "project-1", identity, credential
    )

    assert captured["args"] == ("project-1", provider.value, locator)
    assert captured["host"] == host
    assert captured["repo_access_credential_id"] == credential
    assert repo.identity == identity


def test_add_repository_rejects_invalid_credential_combinations() -> None:
    gateway = PortfolioGateway(client=cast(GatewayClient, object()), auth_port=AuthSessionAdapter())
    with pytest.raises(ValueError, match="only valid for GitLab"):
        gateway.add_repository(
            "project-1", RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"), "cred"
        )
    with pytest.raises(ValueError, match="requires an explicit"):
        gateway.add_repository(
            "project-1", RepositoryIdentity(RepositoryProvider.GITLAB, "group/cat", "gitlab.example"), None
        )


def test_repository_mutation_response_requires_complete_neutral_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    import enji_guard_cli.enji_gateway.portfolio_gateway as module

    monkeypatch.setattr(
        module,
        "_add_project_repo",
        lambda *args, **kwargs: {"id": "repo-1", "projectId": "project-1", "provider": "github"},
    )
    gateway = PortfolioGateway(client=cast(GatewayClient, object()), auth_port=AuthSessionAdapter())
    with pytest.raises(PortfolioMalformedError, match="neutral provider identity"):
        gateway.add_repository("project-1", RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"))
