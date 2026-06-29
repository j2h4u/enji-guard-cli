import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from enji_guard_cli.auth import (
    AUTH_REFRESH_ORIGIN,
    AUTH_REFRESH_REFERER,
    AUTH_REFRESH_USER_AGENT,
    import_bearer_token,
    import_cookie,
    load_stored_auth,
)
from enji_guard_cli.enji_api import EnjiApiError, _get_json_object, access, load_api_session, reports_list
from enji_guard_cli.transport import EnjiHttpError, EnjiHttpRequest, EnjiHttpResponse


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
            headers={"Authorization": "Bearer token-123"},
        )
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
    assert client.requests[0].headers == {"Cookie": "session=abc"}


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

    payload = reports_list(auth_file, client, selector="Pets/*")

    assert payload["projects"] == [
        {
            "id": "project_1",
            "name": "Pets",
            "repo_ids": ["repo_1"],
            "scores": {},
            "recon_pending": None,
        }
    ]


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


def test_access_refreshes_cookie_on_auth_invalid_and_retries_once(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=long", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"error": {"code": "AUTH_INVALID", "message": "invalid access token"}}, status_code=401),
            json_response({"message": "token refreshed"}, set_cookie_headers=("access=new; Path=/; HttpOnly",)),
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
    assert client.requests[0].headers == {"Cookie": "access=old; refresh=long"}
    assert client.requests[1].headers == {
        "Cookie": "access=old; refresh=long",
        "Origin": AUTH_REFRESH_ORIGIN,
        "Referer": AUTH_REFRESH_REFERER,
        "User-Agent": AUTH_REFRESH_USER_AGENT,
    }
    assert client.requests[2].headers == {"Cookie": "access=new; refresh=long"}
    stored_auth = load_stored_auth(auth_file)
    assert stored_auth is not None
    assert stored_auth["credential"] == {"type": "cookie", "cookie_header": "access=new; refresh=long"}


def test_access_surfaces_auth_required_when_refresh_cookie_is_invalid(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_cookie("access=old; refresh=expired", auth_file)
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
    import_cookie("access=old; refresh=long", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"error": {"code": "AUTH_INVALID"}}, status_code=401),
            json_response({"message": "token refreshed"}, set_cookie_headers=("access=new; Path=/",)),
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
    import_cookie("access=old; refresh=long", auth_file)
    session = load_api_session(auth_file)
    client = ConcurrentRefreshFakeClient()

    async def run() -> tuple[dict[str, object], dict[str, object]]:
        return await asyncio.gather(
            _get_json_object(session, client, path="/api/ux/me/access", operation="access"),
            _get_json_object(session, client, path="/api/ux/me/access", operation="access"),
        )

    payloads = asyncio.run(run())

    assert payloads == [
        {"access": {"group": "pro", "fullAccess": True, "limits": {}}},
        {"access": {"group": "pro", "fullAccess": True, "limits": {}}},
    ]
    assert [request.method for request in client.requests].count("POST") == 1
    assert client.requests[-1].headers == {"Cookie": "access=new; refresh=long"}


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


@dataclass
class ConcurrentRefreshFakeClient:
    requests: list[EnjiHttpRequest] = field(default_factory=list)
    get_count: int = 0

    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
        self.requests.append(request)
        if request.method == "POST":
            await asyncio.sleep(0.01)
            return json_response({"message": "token refreshed"}, set_cookie_headers=("access=new; Path=/",))
        self.get_count += 1
        if self.get_count <= 2:
            return json_response({"error": {"code": "AUTH_INVALID"}}, status_code=401)
        return json_response({"access": {"group": "pro", "fullAccess": True, "limits": {}}})
