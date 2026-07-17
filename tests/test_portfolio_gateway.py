from typing import cast

import pytest

from enji_guard_cli.application import Application
from enji_guard_cli.audit.ports import AuditGatewayPort
from enji_guard_cli.auth_session.adapters import AuthSessionAdapter
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.enji_gateway import PortfolioGateway
from enji_guard_cli.enji_gateway.ports import GatewayClient
from enji_guard_cli.json_types import JsonObjectPayload


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
            {"id": "repo-1", "projectId": "project-1", "fullName": "acme/cat", "connected": True},
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
    assert project.repositories[0].full_name == "acme/cat"
    assert project.linked_websites[0].url == "https://pets.example"
    assert project.linked_websites[0].repo_ids == ("repo-1",)


def test_project_detail_uses_nested_collections_as_compatibility_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload: JsonObjectPayload = {
        "project": {
            "id": "project-1",
            "name": "Pets",
            "repos": [{"id": "repo-1", "fullName": "acme/cat"}],
            "webResources": [{"url": "https://pets.example", "repoIds": ["repo-1"]}],
        }
    }
    import enji_guard_cli.enji_gateway.portfolio_gateway as module

    monkeypatch.setattr(module, "_project_detail", lambda *_args, **_kwargs: payload)
    detail = PortfolioGateway(client=cast(GatewayClient, object()), auth_port=AuthSessionAdapter()).project_detail(
        "project-1"
    )

    assert detail.repositories[0].repo_id == "repo-1"
    assert detail.linked_website_repo_ids == {"https://pets.example": ("repo-1",)}


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
