import json
from collections.abc import Mapping, Sequence
from typing import cast

from scripts import release_contract


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self, args: Sequence[str], *, input: str | None = None, timeout: float
    ) -> release_contract.CommandResult:
        del input, timeout
        call = list(args)
        self.calls.append(call)
        if call[1:3] == ["run", "--detach"]:
            return release_contract.CommandResult(0, "container-id\n", "")
        if call[1:3] == ["rm", "--force"]:
            return release_contract.CommandResult(0, "removed\n", "")
        if call[-2:] == ["health", "--ready"]:
            return release_contract.CommandResult(1, "", "UNREADY: authentication is not configured\n")
        if call[-1] == "--help":
            return release_contract.CommandResult(0, "Usage: enji-guard\n", "")
        if call[-1] == "health":
            return release_contract.CommandResult(0, '{"status":"ok"}\n', "")
        raise AssertionError(f"unexpected command: {call}")


def fake_mcp(
    payload: Mapping[str, object],
    headers: Mapping[str, str],
    *,
    url: str,
    timeout: float,
) -> release_contract.McpResponse:
    del headers, url, timeout
    method = payload.get("method")
    request_id = payload.get("id")
    if method == "initialize":
        body = {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-03-26"}}
    elif method == "notifications/initialized":
        return release_contract.McpResponse("", {})
    elif method == "tools/list":
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": [{"name": "enji_portfolio_overview"}, {"name": "enji_repo_audits"}]},
        }
    else:
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "isError": True,
                "structuredContent": {"code": "AUTH_REQUIRED", "message": "credentials are required"},
                "content": [{"text": "authentication required"}],
            },
        }
    return release_contract.McpResponse(json.dumps(body), {"Mcp-Session-Id": "contract-test"})


def test_credentialless_contract_checks_hardened_container_and_cleanup() -> None:
    runner = FakeRunner()
    settings = release_contract.ContractSettings(
        image="enji-guard-cli:test", container="release-contract-test", host_port=18080
    )

    assert release_contract.run_contract(settings, runner=runner, transport_factory=fake_mcp) == 0
    start = runner.calls[0]
    assert "--read-only" in start
    assert start[start.index("--user") + 1] == "1000:1000"
    assert "--cap-drop" in start
    assert all("auth" not in item for item in start)
    assert runner.calls[-1] == ["docker", "rm", "--force", "release-contract-test"]


def test_contract_reports_cleanup_failure() -> None:
    runner = FakeRunner()

    def failing_cleanup(
        args: Sequence[str], *, input: str | None = None, timeout: float
    ) -> release_contract.CommandResult:
        result = runner(args, input=input, timeout=timeout)
        if list(args)[1:3] == ["rm", "--force"]:
            return release_contract.CommandResult(1, "", "cannot remove\n")
        return result

    settings = release_contract.ContractSettings(image="enji-guard-cli:test", container="contract", host_port=18081)
    assert (
        release_contract.run_contract(settings, runner=failing_cleanup, transport_factory=fake_mcp)
        == release_contract.EXIT_CLEANUP
    )


def test_contract_rejects_invalid_timeout() -> None:
    assert release_contract.main(["--image", "enji-guard-cli:test", "--timeout", "0"]) == release_contract.EXIT_CONFIG


def test_contract_accepts_structured_auth_error_result() -> None:
    runner = FakeRunner()

    def error_mcp(
        payload: Mapping[str, object],
        headers: Mapping[str, str],
        *,
        url: str,
        timeout: float,
    ) -> release_contract.McpResponse:
        response = fake_mcp(payload, headers, url=url, timeout=timeout)
        if payload.get("method") == "tools/call":
            body = {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "result": {"isError": True, "structuredContent": {"code": "AUTH_REQUIRED"}},
            }
            return release_contract.McpResponse(json.dumps(body), {})
        return response

    settings = release_contract.ContractSettings(
        image="enji-guard-cli:test", container="contract-error", host_port=18082
    )
    assert release_contract.run_contract(settings, runner=runner, transport_factory=error_mcp) == 0


def test_contract_rejects_non_auth_mcp_error_result() -> None:
    runner = FakeRunner()

    def error_mcp(
        payload: Mapping[str, object],
        headers: Mapping[str, str],
        *,
        url: str,
        timeout: float,
    ) -> release_contract.McpResponse:
        response = fake_mcp(payload, headers, url=url, timeout=timeout)
        if payload.get("method") == "tools/call":
            body = {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "result": {"isError": True, "structuredContent": {"code": "UPSTREAM_FAILED"}},
            }
            return release_contract.McpResponse(json.dumps(body), {})
        return response

    settings = release_contract.ContractSettings(
        image="enji-guard-cli:test", container="contract-error", host_port=18083
    )
    assert (
        release_contract.run_contract(settings, runner=runner, transport_factory=error_mcp)
        == release_contract.EXIT_ASSERTION
    )


def test_contract_rejects_mcp_result_without_explicit_error_flag() -> None:
    runner = FakeRunner()

    def missing_error(
        payload: Mapping[str, object],
        headers: Mapping[str, str],
        *,
        url: str,
        timeout: float,
    ) -> release_contract.McpResponse:
        response = fake_mcp(payload, headers, url=url, timeout=timeout)
        if payload.get("method") == "tools/call":
            body = cast(dict[str, object], cast(object, json.loads(response.body)))
            result = cast(dict[str, object], body["result"])
            result.pop("isError", None)
            return release_contract.McpResponse(json.dumps(body), {})
        return response

    settings = release_contract.ContractSettings(
        image="enji-guard-cli:test", container="contract-missing-error", host_port=18084
    )
    assert (
        release_contract.run_contract(settings, runner=runner, transport_factory=missing_error)
        == release_contract.EXIT_ASSERTION
    )


def test_contract_collision_does_not_remove_container_it_did_not_start() -> None:
    calls: list[list[str]] = []

    def collision_runner(
        args: Sequence[str], *, input: str | None = None, timeout: float
    ) -> release_contract.CommandResult:
        del input, timeout
        call = list(args)
        calls.append(call)
        if call[1:3] == ["run", "--detach"]:
            return release_contract.CommandResult(125, "", "container name is already in use\n")
        raise AssertionError(f"unexpected command after failed start: {call}")

    settings = release_contract.ContractSettings(image="enji-guard-cli:test", container="collision", host_port=18085)
    assert (
        release_contract.run_contract(settings, runner=collision_runner, transport_factory=fake_mcp)
        == release_contract.EXIT_ASSERTION
    )
    assert not any(call[1:3] == ["rm", "--force"] for call in calls)
