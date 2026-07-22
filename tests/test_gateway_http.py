import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from enji_guard_cli.auth_session.adapters import GatewayCredentialReader
from enji_guard_cli.auth_session.api import (
    import_bearer_token,
    import_cookie,
    load_stored_auth,
)
from enji_guard_cli.enji_gateway.client import get_json_object
from enji_guard_cli.enji_gateway.http import (
    AuditRunCreate,
    EnjiApiError,
    EnjiPartialStateError,
    RepoTransfer,
    access,
    add_project_repo,
    audit_auto_runs,
    audit_email_preferences,
    audit_summary_snapshot,
    catalog,
    connect_project_repo,
    create_project,
    delete_project,
    delete_project_repo,
    load_api_session,
    move_repo,
    preflight_repo_move,
    project_detail,
    project_run_language,
    put_audit_auto_run,
    put_audit_email_preferences,
    put_user_language,
    rename_project,
    repo_active_runs,
    repo_audit_rerun_state,
    repo_task_links,
    runbook,
    start_audit_run,
    task_detail,
    user_preferences,
)
from enji_guard_cli.settings import DEFAULT_GUARD_ORIGIN, DEFAULT_GUARD_REFERER
from enji_guard_cli.transport import EnjiHttpError, EnjiHttpRequest, EnjiHttpResponse

AUTH_REFRESH_ORIGIN = DEFAULT_GUARD_ORIGIN
AUTH_REFRESH_REFERER = DEFAULT_GUARD_REFERER
AUTH_PORT = GatewayCredentialReader()


@dataclass
class FakeEnjiHttpClient:
    responses: list[EnjiHttpResponse | EnjiHttpError]
    requests: list[EnjiHttpRequest] = field(default_factory=list)

    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, EnjiHttpError):
            raise response
        return response


def test_access_returns_normalized_payload_and_uses_stored_auth_headers(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response(
                {
                    "access": {
                        "group": "starter",
                        "fullAccess": True,
                        "limits": {
                            "canAddRepo": True,
                            "canAddWebsite": False,
                            "canCreateProject": True,
                            "canInviteMembers": False,
                            "canRunOneShotAutofix": True,
                            "canRunOneShotPentest": False,
                            "canUseSchedules": False,
                            "auditRuns": {
                                "audit.security": {"remaining": 3, "resetAt": "2026-06-29T12:00:00Z"},
                                "audit.tests": True,
                            },
                            "autofixRuns": {
                                "improvement.vuln-fix": {"remaining": 1},
                            },
                        },
                        "usage": [{"kind": "audit", "used": 2}],
                    },
                }
            )
        ]
    )

    payload = access(auth_file, client, auth_port=AUTH_PORT)

    assert payload == {
        "group": "starter",
        "full_access": True,
        "limits": {
            "can_add_repo": True,
            "can_add_website": False,
            "can_create_project": True,
            "can_invite_members": False,
            "can_run_one_shot_autofix": True,
            "can_run_one_shot_pentest": False,
            "can_use_schedules": False,
            "audit_runs": {
                "audit.security": {"remaining": 3, "resetAt": "2026-06-29T12:00:00Z"},
                "audit.tests": True,
            },
            "autofix_runs": {
                "improvement.vuln-fix": {"remaining": 1},
            },
        },
        "usage": [{"kind": "audit", "used": 2}],
    }
    assert client.requests == [
        EnjiHttpRequest(
            method="GET",
            url="https://fleet.enji.ai/api/ux/me/access",
            operation="access",
            headers={"Authorization": "Bearer token-123", "Origin": AUTH_REFRESH_ORIGIN},
        )
    ]


def test_report_language_endpoints_use_observed_paths_and_payloads(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"preferences": {"language": "ru"}}),
            json_response({"language": "ru"}),
            json_response({"preferences": {"language": "en"}}),
        ]
    )

    assert user_preferences(auth_file, client, auth_port=AUTH_PORT) == {"preferences": {"language": "ru"}}
    assert project_run_language("project_1", auth_file, client, auth_port=AUTH_PORT) == {"language": "ru"}
    assert put_user_language("en", auth_file, client, auth_port=AUTH_PORT) == {"preferences": {"language": "en"}}

    assert [(request.method, request.url, request.json_body) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/user-preferences", None),
        ("GET", "https://fleet.enji.ai/api/ux/projects/project_1/run-language", None),
        ("PUT", "https://fleet.enji.ai/api/ux/user-preferences", {"language": "en"}),
    ]


def test_project_admin_and_repo_transfer_operations_use_expected_requests(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"id": "project_1"}, status_code=201),
            json_response({"project": {"id": "project_1", "name": "Pets"}}, status_code=201),
            json_response({"project": {"id": "project_1", "name": "Friends"}}),
            empty_response(status_code=204),
            empty_response(status_code=204),
            empty_response(status_code=204),
            json_response({"repo": {"id": "repo_1", "projectId": "project_2"}}),
            json_response({"repo": {"id": "repo_1", "connected": True}}),
        ]
    )

    created = create_project("Pets", auth_file, client, auth_port=AUTH_PORT)
    renamed = rename_project("project_1", "Friends", auth_file, client, auth_port=AUTH_PORT)
    delete_project("project_1", auth_file, client, auth_port=AUTH_PORT)
    preflight = preflight_repo_move("project_1", "repo_1", "project_2", auth_file, client, auth_port=AUTH_PORT)
    moved = move_repo(RepoTransfer("project_1", "repo_1", "project_2"), auth_file, client, auth_port=AUTH_PORT)
    connected = connect_project_repo("project_1", "repo_1", auth_file, client, auth_port=AUTH_PORT)

    assert created == {"project": {"id": "project_1", "name": "Pets"}}
    assert renamed == {"project": {"id": "project_1", "name": "Friends"}}
    assert preflight == {}
    assert moved == {"repo": {"id": "repo_1", "projectId": "project_2"}}
    assert connected == {"repo": {"id": "repo_1", "connected": True}}
    assert [(request.method, request.url) for request in client.requests] == [
        ("POST", "https://fleet.enji.ai/api/v1/projects"),
        ("POST", "https://fleet.enji.ai/api/ux/projects"),
        ("PATCH", "https://fleet.enji.ai/api/ux/projects/project_1"),
        ("DELETE", "https://fleet.enji.ai/api/ux/projects/project_1"),
        ("DELETE", "https://fleet.enji.ai/api/v1/projects/project_1"),
        ("POST", "https://fleet.enji.ai/api/ux/projects/project_1/repos/repo_1/transfer/preflight"),
        ("POST", "https://fleet.enji.ai/api/ux/projects/project_1/repos/repo_1/transfer"),
        ("PUT", "https://fleet.enji.ai/api/ux/projects/project_1/repos/repo_1/connection"),
    ]
    assert client.requests[0].json_body == {"name": "Pets"}
    ux_create_body = client.requests[1].json_body
    assert isinstance(ux_create_body, dict)
    assert ux_create_body["fleetProjectId"] == "project_1"
    assert ux_create_body["name"] == "Pets"
    assert isinstance(ux_create_body["createdAt"], str)
    assert client.requests[2].json_body == {"name": "Friends"}
    assert client.requests[5].json_body == {"targetProjectId": "project_2"}
    assert client.requests[6].json_body == {"targetProjectId": "project_2"}
    connection_body = client.requests[7].json_body
    assert isinstance(connection_body, dict)
    assert connection_body["connected"] is True
    assert isinstance(connection_body["lastVerifiedAt"], str)


def test_create_project_surfaces_partial_state_when_ux_create_fails(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"id": "project_1"}, status_code=201),
            json_response(
                {"code": "CLIENT_NOT_ALLOWED", "message": "client is not allowed"},
                status_code=403,
            ),
        ]
    )

    try:
        create_project("Pets", auth_file, client, auth_port=AUTH_PORT)
    except EnjiPartialStateError as exc:
        assert exc.code == "PARTIAL_STATE"
        assert exc.operation == "create_project"
        assert exc.completed_step == "fleet_create"
        assert exc.failed_step == "ux_create"
        assert exc.project_id == "project_1"
        assert exc.project_name == "Pets"
        assert exc.upstream_code == "CLIENT_NOT_ALLOWED"
        assert exc.upstream_message == "client is not allowed"
        assert exc.message == (
            "operation=create_project; completed_step=fleet_create; failed_step=ux_create; "
            "project_id=project_1; project_name=Pets; upstream_code=CLIENT_NOT_ALLOWED; "
            "upstream_message=client is not allowed"
        )
    else:
        raise AssertionError("expected EnjiPartialStateError")

    assert [(request.method, request.url) for request in client.requests] == [
        ("POST", "https://fleet.enji.ai/api/v1/projects"),
        ("POST", "https://fleet.enji.ai/api/ux/projects"),
    ]


def test_delete_project_surfaces_partial_state_when_fleet_delete_fails(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(
        [
            empty_response(status_code=204),
            json_response(
                {"code": "CLIENT_NOT_ALLOWED", "message": "client is not allowed"},
                status_code=403,
            ),
        ]
    )

    try:
        delete_project("project_1", auth_file, client, auth_port=AUTH_PORT)
    except EnjiPartialStateError as exc:
        assert exc.code == "PARTIAL_STATE"
        assert exc.operation == "delete_project"
        assert exc.completed_step == "ux_delete"
        assert exc.failed_step == "fleet_delete"
        assert exc.project_id == "project_1"
        assert exc.project_name is None
        assert exc.upstream_code == "CLIENT_NOT_ALLOWED"
        assert exc.upstream_message == "client is not allowed"
        assert exc.message == (
            "operation=delete_project; completed_step=ux_delete; failed_step=fleet_delete; "
            "project_id=project_1; upstream_code=CLIENT_NOT_ALLOWED; "
            "upstream_message=client is not allowed"
        )
    else:
        raise AssertionError("expected EnjiPartialStateError")

    assert [(request.method, request.url) for request in client.requests] == [
        ("DELETE", "https://fleet.enji.ai/api/ux/projects/project_1"),
        ("DELETE", "https://fleet.enji.ai/api/v1/projects/project_1"),
    ]


def test_repo_audit_report_and_schedule_operations_use_expected_requests(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"project": {"id": "project_1"}, "repos": [], "webResources": []}),
            json_response(
                {
                    "curatedActions": [
                        {
                            "actionKey": "audit.recon",
                            "title": "Run recon",
                            "category": "audit",
                            "status": "published",
                            "runbookKind": "recon",
                        },
                        {
                            "actionKey": "audit.security",
                            "title": "Security",
                            "category": "audit",
                            "status": "published",
                            "metricGroup": "security",
                            "runbookKind": "vuln-audit",
                        },
                        {
                            "actionKey": "audit.dependency-hygiene",
                            "title": "Dependency hygiene",
                            "category": "audit",
                            "status": "published",
                            "metricGroup": "dependency-hygiene",
                            "runbookKind": "dependency-audit",
                        },
                    ]
                }
            ),
            json_response({"id": "runbook_1", "suggested_flow": "single"}),
            json_response({"repo": {"id": "repo_1"}}, status_code=201),
            empty_response(status_code=204),
            json_response({"activeRuns": []}),
            json_response({"state": {"currentHeadSha": "abc"}}),
            json_response({"links": []}),
            json_response({"task": {"id": "task_1", "status": "pending"}}),
            json_response({"task": {"id": "task_1"}}, status_code=201),
            json_response({"snapshot": {"content": {"report": "ok"}}}),
            json_response({"subscriptions": []}),
            json_response({"subscription": {"enabled": True}}),
        ]
    )

    project_detail("project_1", auth_file, client, auth_port=AUTH_PORT)
    catalog(auth_file, client, auth_port=AUTH_PORT)
    runbook("runbook_1", auth_file, client, auth_port=AUTH_PORT)
    add_project_repo(
        "project_1", "github", "j2h4u/enji-guard-cli", auth_port=AUTH_PORT, auth_file=auth_file, client=client
    )
    delete_project_repo("project_1", "repo_1", auth_file, client, auth_port=AUTH_PORT)
    repo_active_runs("repo_1", auth_file, client, auth_port=AUTH_PORT)
    repo_audit_rerun_state("repo_1", auth_file, client, auth_port=AUTH_PORT)
    repo_task_links("repo_1", auth_file, client, auth_port=AUTH_PORT)
    task_detail("task_1", auth_file, client, auth_port=AUTH_PORT)
    start_audit_run(
        AuditRunCreate(
            repo_id="repo_1",
            project_id="project_1",
            action_key="audit.recon",
            fleet_task_body={"title": "Run recon"},
        ),
        auth_file,
        client,
        auth_port=AUTH_PORT,
    )
    audit_summary_snapshot("repo_1", "vulns", auth_file, client, task_id="task_1", auth_port=AUTH_PORT)
    audit_auto_runs("repo_1", auth_file, client, auth_port=AUTH_PORT)
    put_audit_auto_run(
        "repo_1",
        "audit.security",
        {
            "cadence": "workdays",
            "enabled": True,
            "scheduleDay": None,
            "scheduleDayOfMonth": 1,
            "scheduleTime": "00:00",
            "scheduleTimeSource": "auto",
            "timezone": "Asia/Almaty",
            "windowDays": [],
            "windowEndTime": None,
            "windowMode": "anytime",
            "windowStartTime": None,
            "unexpected": "ignored",
        },
        auth_file,
        client,
        auth_port=AUTH_PORT,
    )

    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/projects/project_1"),
        ("GET", "https://fleet.enji.ai/api/ux/catalog"),
        ("GET", "https://fleet.enji.ai/api/v1/runbooks/runbook_1"),
        ("POST", "https://fleet.enji.ai/api/ux/projects/project_1/repos"),
        ("DELETE", "https://fleet.enji.ai/api/ux/projects/project_1/repos/repo_1"),
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/active-runs"),
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/audit-rerun-state"),
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/task-links"),
        ("GET", "https://fleet.enji.ai/api/v1/tasks/task_1"),
        ("POST", "https://fleet.enji.ai/api/ux/repos/repo_1/audit-runs"),
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/snapshots/upfront.audit.summary?group=vulns&run=task_1"),
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/audit-auto-runs"),
        ("PUT", "https://fleet.enji.ai/api/ux/repos/repo_1/audit-auto-runs/audit.security"),
    ]
    assert client.requests[3].json_body == {"githubOwner": "j2h4u", "githubName": "enji-guard-cli"}


def test_add_project_repo_gitlab_requires_host_and_credential(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient([EnjiHttpResponse(201, {}, b'{"ok": true}')])
    with pytest.raises(ValueError, match="host and repoAccessCredentialId"):
        add_project_repo("project_1", "gitlab", "group/sub/repo", auth_port=AUTH_PORT, client=client)
    add_project_repo(
        "project_1",
        "gitlab",
        "group/sub/repo",
        host="gitlab.example.com",
        repo_access_credential_id="cred_1",
        auth_file=auth_file,
        auth_port=AUTH_PORT,
        client=client,
    )
    assert client.requests[0].json_body == {
        "provider": "gitlab",
        "host": "gitlab.example.com",
        "repoPath": "group/sub/repo",
        "repoAccessCredentialId": "cred_1",
    }


def test_audit_email_preferences_use_expected_requests(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"resolved": {"manualRunCompletion": True, "scheduledRunCompletion": False}}),
            json_response({"resolved": {"manualRunCompletion": False, "scheduledRunCompletion": False}}),
        ]
    )

    read_payload = audit_email_preferences("repo_1", "audit.security", auth_file, client, auth_port=AUTH_PORT)
    write_payload = put_audit_email_preferences(
        "repo_1",
        "audit.security",
        {"manualRunCompletion": False, "scheduledRunCompletion": False},
        auth_file,
        client,
        auth_port=AUTH_PORT,
    )

    assert read_payload == {"resolved": {"manualRunCompletion": True, "scheduledRunCompletion": False}}
    assert write_payload == {"resolved": {"manualRunCompletion": False, "scheduledRunCompletion": False}}
    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/audits/audit.security/email-preferences"),
        ("PUT", "https://fleet.enji.ai/api/ux/repos/repo_1/audits/audit.security/email-preferences"),
    ]
    assert client.requests[1].json_body == {"manualRunCompletion": False, "scheduledRunCompletion": False}


def test_access_raises_auth_required_when_auth_file_is_missing(tmp_path: Path) -> None:
    try:
        access(tmp_path / "missing.json", FakeEnjiHttpClient([]), auth_port=AUTH_PORT)
    except EnjiApiError as exc:
        assert exc.code == "AUTH_REQUIRED"
        assert exc.message == "auth file does not exist"
    else:
        raise AssertionError("expected EnjiHttpError")


def test_api_error_payload_is_preserved_for_unexpected_status(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response(
                {"code": "CLIENT_NOT_ALLOWED", "message": "client is not allowed"},
                status_code=403,
            )
        ]
    )

    try:
        repo_active_runs("repo_1", auth_file, client, auth_port=AUTH_PORT)
    except EnjiApiError as exc:
        assert exc.code == "CLIENT_NOT_ALLOWED"
        assert exc.message == "client is not allowed"
    else:
        raise AssertionError("expected EnjiApiError")


def test_cookie_permission_forbidden_does_not_refresh(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response(
                {"code": "CLIENT_NOT_ALLOWED", "message": "client is not allowed"},
                status_code=403,
            )
        ]
    )

    try:
        repo_active_runs("repo_1", auth_file, client, auth_port=AUTH_PORT)
    except EnjiApiError as exc:
        assert exc.code == "CLIENT_NOT_ALLOWED"
        assert exc.message == "client is not allowed"
    else:
        raise AssertionError("expected EnjiApiError")

    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/active-runs")
    ]


def test_cookie_auth_invalid_forbidden_is_not_refreshed_or_replayed(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"error": {"code": "AUTH_INVALID"}}, status_code=403),
        ]
    )

    with pytest.raises(EnjiApiError) as exc_info:
        repo_active_runs("repo_1", auth_file, client, auth_port=AUTH_PORT)
    assert exc_info.value.code == "AUTH_INVALID"
    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/active-runs"),
    ]


def test_gateway_cookie_auth_invalid_is_not_refreshed_or_replayed(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    client = FakeEnjiHttpClient([json_response({"error": {"code": "AUTH_INVALID"}}, status_code=401)])

    with pytest.raises(EnjiApiError, match="invalid access token"):
        access(auth_file, client, auth_port=AUTH_PORT)

    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/me/access")
    ]
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access_token=old; refresh_token=long"}


def test_gateway_concurrent_auth_invalid_requests_issue_no_refresh_posts(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    session = load_api_session(auth_file, auth_port=AUTH_PORT)
    client = ConcurrentRefreshFakeClient()

    async def run() -> None:
        results = await asyncio.gather(
            get_json_object(session, client, path="/api/ux/me/access", operation="access"),
            get_json_object(session, client, path="/api/ux/me/access", operation="access"),
            return_exceptions=True,
        )
        assert all(isinstance(result, EnjiHttpError) for result in results)

    asyncio.run(run())
    assert [request.method for request in client.requests] == ["GET", "GET"]


def json_response(
    payload: object,
    *,
    status_code: int = 200,
    set_cookie_headers: tuple[str, ...] = (),
) -> EnjiHttpResponse:
    return EnjiHttpResponse(
        status_code=status_code,
        headers={},
        content=json.dumps(payload).encode("utf-8"),
        set_cookie_headers=set_cookie_headers,
    )


def empty_response(*, status_code: int = 204) -> EnjiHttpResponse:
    return EnjiHttpResponse(status_code=status_code, headers={}, content=b"")


@dataclass
class ConcurrentRefreshFakeClient:
    requests: list[EnjiHttpRequest] = field(default_factory=list)
    get_count: int = 0
    second_invalid_seen: asyncio.Event = field(default_factory=asyncio.Event)

    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
        self.requests.append(request)
        if request.method == "POST":
            await self.second_invalid_seen.wait()
            return json_response(
                {"message": "token refreshed"},
                set_cookie_headers=(
                    "access_token=new; Path=/",
                    "refresh_token=new-refresh; Path=/api/v1/auth",
                ),
            )
        self.get_count += 1
        if self.get_count <= 2:
            if self.get_count == 2:
                self.second_invalid_seen.set()
            return json_response({"error": {"code": "AUTH_INVALID"}}, status_code=401)
        return json_response({"access": {"group": "pro", "fullAccess": True, "limits": {}}})
