import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from enji_guard_cli.auth import (
    AUTH_REFRESH_USER_AGENT,
    import_bearer_token,
    import_cookie,
    load_stored_auth,
)
from enji_guard_cli.enji_api import (
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
    reports_list,
    runbook,
    start_audit_run,
    task_detail,
    user_preferences,
)
from enji_guard_cli.enji_api_impl.client import get_json_object
from enji_guard_cli.settings import DEFAULT_GUARD_ORIGIN, DEFAULT_GUARD_REFERER
from enji_guard_cli.transport import EnjiHttpError, EnjiHttpRequest, EnjiHttpResponse

AUTH_REFRESH_ORIGIN = DEFAULT_GUARD_ORIGIN
AUTH_REFRESH_REFERER = DEFAULT_GUARD_REFERER


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

    payload = access(auth_file, client)

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

    assert user_preferences(auth_file, client) == {"preferences": {"language": "ru"}}
    assert project_run_language("project_1", auth_file, client) == {"language": "ru"}
    assert put_user_language("en", auth_file, client) == {"preferences": {"language": "en"}}

    assert [(request.method, request.url, request.json_body) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/user-preferences", None),
        ("GET", "https://fleet.enji.ai/api/ux/projects/project_1/run-language", None),
        ("PUT", "https://fleet.enji.ai/api/ux/user-preferences", {"language": "en"}),
    ]


def test_reports_list_returns_compact_project_overview(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("session=abc", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response(
                {
                    "projects": [
                        {
                            "id": "project_1",
                            "name": "Pets",
                            "repoIds": ["repo_1", "repo_2", 7],
                            "scores": {
                                "security": {"critical": 1, "high": 2},
                                "health": 98.5,
                            },
                            "reconPending": True,
                            "ignored": "value",
                        },
                        {
                            "id": 9,
                            "name": None,
                            "repoIds": "bad-shape",
                            "scores": [],
                            "reconPending": "later",
                        },
                    ]
                }
            )
        ]
    )

    payload = reports_list(auth_file, client)

    assert payload == {
        "projects": [
            {
                "id": "project_1",
                "name": "Pets",
                "repo_ids": ["repo_1", "repo_2"],
                "scores": {
                    "security": {"critical": 1, "high": 2},
                    "health": 98.5,
                },
                "recon_pending": True,
            },
            {
                "id": None,
                "name": None,
                "repo_ids": [],
                "scores": {},
                "recon_pending": None,
            },
        ]
    }
    assert client.requests[0].headers == {"Cookie": "session=abc", "Origin": AUTH_REFRESH_ORIGIN}


def test_reports_list_filters_project_selector(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("session=abc", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response(
                {
                    "projects": [
                        {"id": "project_1", "name": "Pets", "repoIds": ["repo_1"]},
                        {"id": "project_2", "name": "Work", "repoIds": ["repo_2"]},
                    ]
                }
            )
        ]
    )

    payload = reports_list(auth_file, client, selector="pets/*")

    assert payload["projects"] == [
        {
            "id": "project_1",
            "name": "Pets",
            "repo_ids": ["repo_1"],
            "scores": {},
            "recon_pending": None,
        }
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

    created = create_project("Pets", auth_file, client)
    renamed = rename_project("project_1", "Friends", auth_file, client)
    delete_project("project_1", auth_file, client)
    preflight = preflight_repo_move("project_1", "repo_1", "project_2", auth_file, client)
    moved = move_repo(RepoTransfer("project_1", "repo_1", "project_2"), auth_file, client)
    connected = connect_project_repo("project_1", "repo_1", auth_file, client)

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
        create_project("Pets", auth_file, client)
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
        delete_project("project_1", auth_file, client)
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

    project_detail("project_1", auth_file, client)
    catalog(auth_file, client)
    runbook("runbook_1", auth_file, client)
    add_project_repo("project_1", "j2h4u", "enji-guard-cli", auth_file, client)
    delete_project_repo("project_1", "repo_1", auth_file, client)
    repo_active_runs("repo_1", auth_file, client)
    repo_audit_rerun_state("repo_1", auth_file, client)
    repo_task_links("repo_1", auth_file, client)
    task_detail("task_1", auth_file, client)
    start_audit_run(
        AuditRunCreate(
            repo_id="repo_1",
            project_id="project_1",
            action_key="audit.recon",
            fleet_task_body={"title": "Run recon"},
        ),
        auth_file,
        client,
    )
    audit_summary_snapshot("repo_1", "vulns", auth_file, client)
    audit_auto_runs("repo_1", auth_file, client)
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
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/snapshots/upfront.audit.summary?group=vulns"),
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/audit-auto-runs"),
        ("PUT", "https://fleet.enji.ai/api/ux/repos/repo_1/audit-auto-runs/audit.security"),
    ]
    assert client.requests[3].json_body == {"githubOwner": "j2h4u", "githubName": "enji-guard-cli"}
    audit_body = client.requests[9].json_body
    assert isinstance(audit_body, dict)
    assert audit_body["projectId"] == "project_1"
    assert audit_body["actionKey"] == "audit.recon"
    assert audit_body["fleetTaskBody"] == {"title": "Run recon"}
    assert isinstance(audit_body["clientRequestId"], str)
    assert client.requests[12].json_body == {
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

    read_payload = audit_email_preferences("repo_1", "audit.security", auth_file, client)
    write_payload = put_audit_email_preferences(
        "repo_1",
        "audit.security",
        {"manualRunCompletion": False, "scheduledRunCompletion": False},
        auth_file,
        client,
    )

    assert read_payload == {"resolved": {"manualRunCompletion": True, "scheduledRunCompletion": False}}
    assert write_payload == {"resolved": {"manualRunCompletion": False, "scheduledRunCompletion": False}}
    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/audits/audit.security/email-preferences"),
        ("PUT", "https://fleet.enji.ai/api/ux/repos/repo_1/audits/audit.security/email-preferences"),
    ]
    assert client.requests[1].json_body == {"manualRunCompletion": False, "scheduledRunCompletion": False}


def test_reports_list_rejects_unsupported_filters(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("session=abc", auth_file)

    try:
        reports_list(auth_file, FakeEnjiHttpClient([]), stale=True)
    except EnjiApiError as exc:
        assert exc.code == "VALIDATION"
        assert exc.message == "stale filtering is not available in the compact project overview yet"
    else:
        raise AssertionError("expected EnjiApiError")


def test_access_raises_auth_required_when_auth_file_is_missing(tmp_path: Path) -> None:
    try:
        access(tmp_path / "missing.json", FakeEnjiHttpClient([]))
    except EnjiApiError as exc:
        assert exc.code == "AUTH_REQUIRED"
        assert exc.message == "auth file does not exist"
    else:
        raise AssertionError("expected EnjiHttpError")


def test_reports_list_raises_upstream_error_for_non_object_response(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient([json_response(["unexpected"])])

    try:
        reports_list(auth_file, client)
    except EnjiApiError as exc:
        assert exc.code == "UPSTREAM"
        assert exc.message == "reports list returned unexpected JSON"
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
        repo_active_runs("repo_1", auth_file, client)
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
        repo_active_runs("repo_1", auth_file, client)
    except EnjiApiError as exc:
        assert exc.code == "CLIENT_NOT_ALLOWED"
        assert exc.message == "client is not allowed"
    else:
        raise AssertionError("expected EnjiApiError")

    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/active-runs")
    ]


def test_cookie_auth_invalid_forbidden_refreshes(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"error": {"code": "AUTH_INVALID"}}, status_code=403),
            json_response(
                {"message": "token refreshed"},
                set_cookie_headers=(
                    "access_token=new; Path=/; HttpOnly",
                    "refresh_token=new-refresh; Path=/api/v1/auth; HttpOnly",
                ),
            ),
            json_response({"activeRuns": []}),
        ]
    )

    payload = repo_active_runs("repo_1", auth_file, client)

    assert payload == {"activeRuns": []}
    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/active-runs"),
        ("POST", "https://fleet.enji.ai/api/v1/auth/refresh"),
        ("GET", "https://fleet.enji.ai/api/ux/repos/repo_1/active-runs"),
    ]
    assert client.requests[2].headers == {
        "Cookie": "access_token=new; refresh_token=new-refresh",
        "Origin": AUTH_REFRESH_ORIGIN,
    }


def test_access_refreshes_cookie_on_auth_invalid_and_retries_once(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"error": {"code": "AUTH_INVALID", "message": "invalid access token"}}, status_code=401),
            json_response(
                {"message": "token refreshed"},
                set_cookie_headers=(
                    "access_token=new; Path=/; HttpOnly",
                    "refresh_token=new-refresh; Path=/api/v1/auth; HttpOnly",
                ),
            ),
            json_response({"access": {"group": "pro", "fullAccess": True, "limits": {"canUseSchedules": True}}}),
        ]
    )

    payload = access(auth_file, client)

    assert payload["group"] == "pro"
    assert payload["limits"]["can_use_schedules"] is True
    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/me/access"),
        ("POST", "https://fleet.enji.ai/api/v1/auth/refresh"),
        ("GET", "https://fleet.enji.ai/api/ux/me/access"),
    ]
    assert client.requests[0].headers == {
        "Cookie": "access_token=old; refresh_token=long",
        "Origin": AUTH_REFRESH_ORIGIN,
    }
    assert client.requests[1].headers == {
        "Cookie": "access_token=old; refresh_token=long",
        "Origin": AUTH_REFRESH_ORIGIN,
        "Referer": AUTH_REFRESH_REFERER,
        "User-Agent": AUTH_REFRESH_USER_AGENT,
    }
    assert client.requests[2].headers == {
        "Cookie": "access_token=new; refresh_token=new-refresh",
        "Origin": AUTH_REFRESH_ORIGIN,
    }
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {
        "type": "cookie",
        "cookie_header": "access_token=new; refresh_token=new-refresh",
    }


def test_access_refreshes_cookie_on_plain_unauthorized_and_retries_once(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"message": "unauthorized"}, status_code=401),
            json_response(
                {"message": "token refreshed"},
                set_cookie_headers=(
                    "access_token=new; Path=/; HttpOnly",
                    "refresh_token=new-refresh; Path=/api/v1/auth; HttpOnly",
                ),
            ),
            json_response({"access": {"group": "pro", "fullAccess": True, "limits": {"canUseSchedules": True}}}),
        ]
    )

    payload = access(auth_file, client)

    assert payload["group"] == "pro"
    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/me/access"),
        ("POST", "https://fleet.enji.ai/api/v1/auth/refresh"),
        ("GET", "https://fleet.enji.ai/api/ux/me/access"),
    ]
    assert client.requests[2].headers == {
        "Cookie": "access_token=new; refresh_token=new-refresh",
        "Origin": AUTH_REFRESH_ORIGIN,
    }


def test_access_surfaces_auth_required_when_refresh_cookie_is_invalid(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=expired", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"error": {"code": "AUTH_INVALID"}}, status_code=401),
            json_response({"error": {"code": "AUTH_REQUIRED"}}, status_code=401),
        ]
    )

    try:
        access(auth_file, client)
    except EnjiApiError as exc:
        assert exc.code == "AUTH_REQUIRED"
        assert exc.message == "stored refresh cookie is not authenticated"
    else:
        raise AssertionError("expected EnjiApiError")

    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/me/access"),
        ("POST", "https://fleet.enji.ai/api/v1/auth/refresh"),
    ]


def test_bearer_token_auth_invalid_does_not_use_cookie_refresh(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient([json_response({"error": {"code": "AUTH_INVALID"}}, status_code=401)])

    try:
        access(auth_file, client)
    except EnjiApiError as exc:
        assert exc.code == "AUTH_INVALID"
        assert exc.message == "invalid access token"
    else:
        raise AssertionError("expected EnjiApiError")

    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/me/access")
    ]


def test_access_retries_only_once_after_cookie_refresh(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"error": {"code": "AUTH_INVALID"}}, status_code=401),
            json_response(
                {"message": "token refreshed"},
                set_cookie_headers=(
                    "access_token=new; Path=/",
                    "refresh_token=new-refresh; Path=/api/v1/auth",
                ),
            ),
            json_response({"error": {"code": "AUTH_INVALID"}}, status_code=401),
        ]
    )

    try:
        access(auth_file, client)
    except EnjiApiError as exc:
        assert exc.code == "AUTH_INVALID"
        assert exc.message == "invalid access token after refresh"
    else:
        raise AssertionError("expected EnjiApiError")

    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/ux/me/access"),
        ("POST", "https://fleet.enji.ai/api/v1/auth/refresh"),
        ("GET", "https://fleet.enji.ai/api/ux/me/access"),
    ]


def test_concurrent_auth_invalid_responses_dedupe_cookie_refresh(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access_token=old; refresh_token=long", auth_file)
    session = load_api_session(auth_file)
    client = ConcurrentRefreshFakeClient()

    async def run() -> tuple[dict[str, object], dict[str, object]]:
        return await asyncio.gather(
            get_json_object(session, client, path="/api/ux/me/access", operation="access"),
            get_json_object(session, client, path="/api/ux/me/access", operation="access"),
        )

    payloads = asyncio.run(run())

    assert payloads == [
        {"access": {"group": "pro", "fullAccess": True, "limits": {}}},
        {"access": {"group": "pro", "fullAccess": True, "limits": {}}},
    ]
    assert [request.method for request in client.requests].count("POST") == 1
    assert client.requests[-1].headers == {
        "Cookie": "access_token=new; refresh_token=new-refresh",
        "Origin": AUTH_REFRESH_ORIGIN,
    }


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
