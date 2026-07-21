import json
from collections.abc import Mapping, Sequence
from typing import cast

import pytest
from scripts import release_smoke


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], *, input: str | None = None, timeout: float) -> release_smoke.CommandResult:
        del input, timeout
        call = list(args)
        self.calls.append(call)
        if call[1:3] == ["inspect", "--format={{.State.Health.Status}}"]:
            result = release_smoke.CommandResult(0, "healthy\n", "")
        elif "__release_smoke_invalid__" in call:
            result = release_smoke.CommandResult(2, "", "sort must be one of: default, name\n")
        elif "--help" in call:
            result = release_smoke.CommandResult(0, "Usage: enji-guard\n", "")
        elif call[-2:] == ["owner/repo", "--json"]:
            payload = [
                {
                    "repository": {"full_name": "owner/repo"},
                    "audit": {
                        "summary": {
                            "current_head_sha": "abc123",
                            "items": [
                                {
                                    "audit_key": "audit.security",
                                    "can_read": True,
                                    "task_lifecycle": "completed",
                                    "freshness": {"state": "fresh"},
                                }
                            ],
                        }
                    },
                }
            ]
            result = release_smoke.CommandResult(0, json.dumps(payload), "")
        elif "--json" in call:
            result = release_smoke.CommandResult(0, json.dumps({"status": "ready"}), "")
        elif call[-2:] == ["status", "owner/repo"]:
            result = release_smoke.CommandResult(
                0,
                "repository: owner/repo\ncurrent_head: abc123\naudits: total=1 ready=1 active=0 stale=0 failed=0\n"
                "  security  state=ready freshness=fresh\n",
                "",
            )
        else:
            result = release_smoke.CommandResult(0, "ok\n", "")
        return result


class RecordingMcp:
    def __init__(self) -> None:
        self.payloads: list[Mapping[str, object]] = []

    def __call__(
        self,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
        *,
        url: str,
        timeout: float,
    ) -> release_smoke.McpResponse:
        self.payloads.append(payload)
        return fake_mcp(payload, headers, url=url, timeout=timeout)


def fake_mcp(
    payload: Mapping[str, object], headers: Mapping[str, str], *, url: str, timeout: float
) -> release_smoke.McpResponse:
    del headers, url, timeout
    method = payload.get("method")
    request_id = payload.get("id")
    if method == "initialize":
        return release_smoke.McpResponse(
            json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-03-26"}}),
            {"Mcp-Session-Id": "test-session"},
        )
    if method == "notifications/initialized":
        return release_smoke.McpResponse("", {})
    if method == "tools/list":
        tools = [{"name": "enji_portfolio_overview"}, {"name": "enji_repo_audits"}]
        return release_smoke.McpResponse(
            json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}), {}
        )
    return release_smoke.McpResponse(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }
        ),
        {},
    )


def test_read_only_smoke_checks_docker_cli_and_exact_mcp_surface() -> None:
    runner = FakeRunner()
    mcp = RecordingMcp()
    settings = release_smoke.DockerSmokeSettings(repo="owner/repo", project="Release")

    assert release_smoke.run_smoke(settings, runner=runner, transport_factory=mcp) == 0
    assert any("inspect" in call for call in runner.calls)
    assert any("status" in call for call in runner.calls)
    notification = next(payload for payload in mcp.payloads if payload.get("method") == "notifications/initialized")
    assert "id" not in notification


def test_smoke_rejects_mcp_tool_drift() -> None:
    runner = FakeRunner()

    def drifted_mcp(
        payload: Mapping[str, object], headers: Mapping[str, str], *, url: str, timeout: float
    ) -> release_smoke.McpResponse:
        response = fake_mcp(payload, headers, url=url, timeout=timeout)
        if payload.get("method") == "tools/list":
            return release_smoke.McpResponse(
                json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {"tools": [{"name": "write_tool"}]}}),
                {},
            )
        return response

    assert (
        release_smoke.run_smoke(
            release_smoke.DockerSmokeSettings(repo="owner/repo"), runner=runner, transport_factory=drifted_mcp
        )
        == 1
    )


def test_invalid_timeout_is_configuration_failure() -> None:
    assert release_smoke.main(["--repo", "owner/repo", "--timeout", "0"]) == release_smoke.EXIT_CONFIG


@pytest.mark.parametrize("argv", [["--repo", "owner/repo"], ["--repo", "owner/repo", "--project", ""]])
def test_parser_accepts_explicit_target(argv: list[str]) -> None:
    args = release_smoke.build_parser().parse_args(argv)
    assert cast(str, args.repo) == "owner/repo"


@pytest.mark.parametrize("variant", ["error", "missing", "empty", "malformed"])
def test_authenticated_mcp_smoke_rejects_invalid_tool_result(variant: str) -> None:
    runner = FakeRunner()

    def invalid_mcp(
        payload: Mapping[str, object], headers: Mapping[str, str], *, url: str, timeout: float
    ) -> release_smoke.McpResponse:
        response = fake_mcp(payload, headers, url=url, timeout=timeout)
        if payload.get("method") != "tools/call":
            return response
        body = cast(dict[str, object], cast(object, json.loads(response.body)))
        if variant == "error":
            body["result"] = {"isError": True, "content": [{"type": "text", "text": "failed"}]}
        elif variant == "missing":
            body["result"] = {}
        elif variant == "empty":
            body["result"] = {"content": []}
        else:
            body["result"] = []
        return release_smoke.McpResponse(json.dumps(body), {})

    assert (
        release_smoke.run_smoke(
            release_smoke.DockerSmokeSettings(repo="owner/repo"),
            runner=runner,
            transport_factory=invalid_mcp,
        )
        == release_smoke.EXIT_ASSERTION
    )


def test_status_json_rejects_inconsistent_lifecycle_fields() -> None:
    payload = [
        {
            "repository": {"full_name": "owner/repo"},
            "audit": {
                "summary": {
                    "current_head_sha": "abc123",
                    "items": [
                        {
                            "audit_key": "audit.security",
                            "state": "ready",
                            "can_read": False,
                            "task_lifecycle": "completed",
                            "freshness": {"state": "fresh"},
                        }
                    ],
                }
            },
        }
    ]
    with pytest.raises(release_smoke.SmokeFailure, match="lifecycle fields disagree"):
        release_smoke._status_json_signature(payload)


def test_status_parity_rejects_freshness_mismatch() -> None:
    runner = FakeRunner()

    def stale_json(args: Sequence[str], *, input: str | None = None, timeout: float) -> release_smoke.CommandResult:
        result = runner(args, input=input, timeout=timeout)
        if list(args)[-2:] == ["owner/repo", "--json"]:
            payload = cast(list[object], json.loads(result.stdout))
            item = cast(dict[str, object], cast(dict[str, object], payload[0])["audit"])
            summary = cast(dict[str, object], item["summary"])
            items = cast(list[object], summary["items"])
            first = cast(dict[str, object], items[0])
            first["freshness"] = {"state": "stale"}
            return release_smoke.CommandResult(0, json.dumps(payload), "")
        return result

    assert (
        release_smoke.run_smoke(
            release_smoke.DockerSmokeSettings(repo="owner/repo"),
            runner=stale_json,
            transport_factory=fake_mcp,
        )
        == release_smoke.EXIT_ASSERTION
    )
