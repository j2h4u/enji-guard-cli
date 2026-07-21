#!/usr/bin/env -S uv run --script
"""Credentialless release contract for a local Enji Guard image.

This contract intentionally creates an isolated disposable container.  It has
no auth mount or credential environment and only exercises the local CLI/MCP
surfaces; upstream calls therefore fail at the normal authentication boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from .release_smoke import CommandResult, McpResponse, http_mcp_transport, subprocess_runner

EXIT_OK = 0
EXIT_ASSERTION = 1
EXIT_CONFIG = 2
EXIT_CLEANUP = 4
CONTAINER_PORT = 8000
MIN_PORT = 1
MAX_PORT = 65535
AUTH_MARKERS = ("auth", "credential", "token", "cookie", "login", "unauthenticated")


@dataclass(frozen=True, slots=True)
class ContractSettings:
    """Immutable settings for one disposable, credentialless container."""

    image: str
    container: str
    host_port: int
    timeout_seconds: float = 20.0
    health_retries: int = 10


class CommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        input: str | None = None,
        timeout: float,
    ) -> CommandResult: ...


class McpTransport(Protocol):
    def __call__(
        self, payload: Mapping[str, object], headers: Mapping[str, str], *, url: str, timeout: float
    ) -> McpResponse: ...


class ContractError(RuntimeError):
    """A release contract assertion failed."""


@dataclass(slots=True)
class _McpSession:
    headers: dict[str, str]
    request_id: int = 0


def _safe_error(value: str) -> str:
    del value
    return "command returned an error"


def _has_auth_marker(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in AUTH_MARKERS)


def _container_exec(settings: ContractSettings, *command: str) -> list[str]:
    return ["docker", "exec", "-i", settings.container, "enji-guard", *command]


def _start_args(settings: ContractSettings) -> list[str]:
    return [
        "docker",
        "run",
        "--detach",
        "--name",
        settings.container,
        "--read-only",
        "--user",
        "1000:1000",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m",  # noqa: S108 - disposable container tmpfs.
        "--tmpfs",
        "/home/app/.config:rw,nosuid,nodev,noexec,size=8m",
        "--publish",
        f"127.0.0.1:{settings.host_port}:{CONTAINER_PORT}",
        settings.image,
        "run",
        "--transport",
        "streamable-http",
        "--host",
        "0.0.0.0",  # noqa: S104 - container listener is restricted by the host publish binding.
        "--port",
        str(CONTAINER_PORT),
        "--allow-external-host",
    ]


def _decode_rpc(response: McpResponse) -> dict[str, object]:
    body = response.body.strip()
    if body.startswith("{"):
        value = cast(object, json.loads(body))
    else:
        events = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        if not events:
            raise ContractError("MCP returned no JSON-RPC event")
        value = cast(object, json.loads(events[-1]))
    if not isinstance(value, dict):
        raise ContractError("MCP returned a non-object JSON-RPC payload")
    return cast(dict[str, object], value)


def _flatten_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _mcp_request(
    settings: ContractSettings,
    transport_factory: McpTransport,
    session: _McpSession,
    method: str,
    params: Mapping[str, object],
) -> dict[str, object]:
    is_notification = method.startswith("notifications/")
    payload: dict[str, object] = {"jsonrpc": "2.0", "method": method, "params": dict(params)}
    if not is_notification:
        session.request_id += 1
        payload["id"] = session.request_id
    response = transport_factory(
        payload,
        session.headers,
        url=f"http://127.0.0.1:{settings.host_port}/mcp",
        timeout=settings.timeout_seconds,
    )
    session_id = next((v for k, v in response.headers.items() if k.casefold() == "mcp-session-id"), None)
    if session_id:
        session.headers["Mcp-Session-Id"] = session_id
    if is_notification and not response.body.strip():
        return {}
    return _decode_rpc(response)


def _mcp_contract(settings: ContractSettings, transport_factory: McpTransport) -> None:
    session = _McpSession({})
    initialized = _mcp_request(
        settings,
        transport_factory,
        session,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "release-contract", "version": "1"},
        },
    )
    if not isinstance(initialized.get("result"), dict):
        raise ContractError("MCP initialize returned no result")
    _mcp_request(settings, transport_factory, session, "notifications/initialized", {})
    listed_response = _mcp_request(
        settings,
        transport_factory,
        session,
        "tools/list",
        {},
    )
    listed = listed_response.get("result")
    if not isinstance(listed, dict) or not isinstance(listed.get("tools"), list):
        raise ContractError("MCP tools/list returned no tools")
    names = {tool.get("name") for tool in listed["tools"] if isinstance(tool, dict)}
    expected = {"enji_portfolio_overview", "enji_repo_audits"}
    if names != expected:
        raise ContractError(f"MCP tool set mismatch: {sorted(str(name) for name in names)}")
    print("PASS MCP exact 2 read-only tools")

    for name, arguments in (
        ("enji_portfolio_overview", {}),
        ("enji_repo_audits", {"repo": "__release_contract__/__no_auth__"}),
    ):
        response = _mcp_request(
            settings,
            transport_factory,
            session,
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise ContractError(f"MCP {name} returned no result")
        if result.get("isError") is not True:
            raise ContractError(f"MCP {name} did not return an error result")
        structured = result.get("structuredContent")
        content = result.get("content")
        error_text = " ".join(
            payload for payload in (_flatten_text(structured), _flatten_text(content)) if payload.strip()
        )
        if not _has_auth_marker(error_text):
            raise ContractError(f"MCP {name} error result was not a clear authentication error")
        print(f"PASS MCP auth error {name}")


def _wait_for_auth_failure(settings: ContractSettings, runner: CommandRunner) -> None:
    deadline = time.monotonic() + settings.timeout_seconds
    attempts = 0
    last = ""
    while attempts < settings.health_retries and time.monotonic() < deadline:
        result = runner(_container_exec(settings, "health", "--ready"), timeout=settings.timeout_seconds)
        last = result.stderr or result.stdout
        if result.returncode != 0 and _has_auth_marker(last):
            print("PASS health --ready fails clearly without auth")
            return
        attempts += 1
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    raise ContractError(f"health --ready did not produce an authentication failure: {_safe_error(last)}")


def _cleanup_contract(settings: ContractSettings, runner: CommandRunner, started: bool) -> bool:
    if not started:
        print("PASS cleanup skipped; container was not created")
        return False
    try:
        removed = runner(["docker", "rm", "--force", settings.container], timeout=settings.timeout_seconds)
    except OSError, subprocess.TimeoutExpired:
        print(f"FAIL cleanup {settings.container}: command returned an error", file=sys.stderr)
        return True
    if removed.returncode != 0:
        print(f"FAIL cleanup {settings.container}: {_safe_error(removed.stderr)}", file=sys.stderr)
        return True
    print(f"PASS cleanup {settings.container}")
    return False


def run_contract(
    settings: ContractSettings,
    *,
    runner: CommandRunner = subprocess_runner,
    transport_factory: McpTransport = http_mcp_transport,
) -> int:
    if (
        not settings.image.strip()
        or settings.host_port < MIN_PORT
        or settings.host_port > MAX_PORT
        or settings.timeout_seconds <= 0
        or settings.health_retries <= 0
    ):
        return EXIT_CONFIG
    started = False
    failure: ContractError | None = None
    try:
        started_result = runner(_start_args(settings), timeout=settings.timeout_seconds)
        if started_result.returncode != 0:
            raise ContractError(f"docker run failed: {_safe_error(started_result.stderr)}")
        started = True
        print(f"PASS started {settings.container}")

        help_result = runner(_container_exec(settings, "--help"), timeout=settings.timeout_seconds)
        if help_result.returncode != 0:
            raise ContractError(f"CLI help failed: {_safe_error(help_result.stderr)}")
        print("PASS CLI help")

        health_result = runner(_container_exec(settings, "health"), timeout=settings.timeout_seconds)
        if health_result.returncode != 0:
            raise ContractError(f"health (non-ready) failed: {_safe_error(health_result.stderr)}")
        print("PASS health (non-ready)")
        _wait_for_auth_failure(settings, runner)
        _mcp_contract(settings, transport_factory)
    except ContractError as exc:
        failure = exc
        print(f"FAIL release contract: {failure}", file=sys.stderr)
    except OSError, subprocess.TimeoutExpired, ValueError:
        failure = ContractError("release contract command failed")
        print(f"FAIL release contract: {failure}", file=sys.stderr)
    finally:
        cleanup_failed = _cleanup_contract(settings, runner, started)
    if cleanup_failed:
        return EXIT_CLEANUP
    return EXIT_ASSERTION if failure is not None else EXIT_OK


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        address = cast(tuple[str, int], sock.getsockname())
        return address[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Local image tag to contract-test")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--container", default="")
    parser.add_argument("--port", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    image = cast(str, args.image)
    timeout = float(cast(float, args.timeout))
    if timeout <= 0:
        return EXIT_CONFIG
    port = int(cast(int, args.port)) or _free_port()
    if not MIN_PORT <= port <= MAX_PORT:
        return EXIT_CONFIG
    supplied = cast(str, args.container)
    container = supplied or f"enji-guard-release-contract-{os.getpid()}-{uuid.uuid4().hex[:10]}"
    return run_contract(ContractSettings(image=image, container=container, host_port=port, timeout_seconds=timeout))


if __name__ == "__main__":
    raise SystemExit(main())
