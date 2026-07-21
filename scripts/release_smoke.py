#!/usr/bin/env -S uv run --script
"""Read-only release smoke checks for the running Enji Guard container.

The script intentionally talks only to the public Docker, CLI, and MCP
surfaces.  It does not import application internals and never prints command
output that could contain credentials.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.response import addinfourl

EXIT_OK = 0
EXIT_ASSERTION = 1
EXIT_CONFIG = 2


@dataclass(frozen=True, slots=True)
class DockerSmokeSettings:
    """Target and safety settings for a read-only smoke run."""

    repo: str
    project: str | None = None
    container: str = "enji-guard-cli"
    mcp_url: str = "http://127.0.0.1:8001/mcp"
    timeout_seconds: float = 15.0
    recreate: bool = False
    auth_persistence: bool = False
    compose_file: str = "docker-compose.yml"


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        input: str | None = None,
        timeout: float,
    ) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class McpResponse:
    body: str
    headers: Mapping[str, str]


class McpTransport(Protocol):
    def __call__(self, payload: Mapping[str, object], headers: Mapping[str, str]) -> McpResponse: ...


def subprocess_runner(args: Sequence[str], *, input: str | None = None, timeout: float) -> CommandResult:
    completed = subprocess.run(  # noqa: S603 - argv is assembled from fixed operators and explicit user selectors.
        list(args),
        input=input,
        capture_output=True,
        check=False,
        shell=False,
        text=True,
        timeout=timeout,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def http_mcp_transport(
    payload: Mapping[str, object], headers: Mapping[str, str], *, url: str, timeout: float
) -> McpResponse:
    parsed_url = urlsplit(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("MCP URL must be an absolute http(s) URL")
    request_headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **headers}
    request = Request(url, data=json.dumps(payload).encode(), headers=request_headers, method="POST")  # noqa: S310
    with cast(addinfourl, urlopen(request, timeout=timeout)) as response:  # noqa: S310 - explicit operator URL.
        raw_headers = dict(response.headers.items())
        return McpResponse(response.read().decode("utf-8"), raw_headers)


class SmokeError(RuntimeError):
    """An assertion failed against a live surface."""


# Keep the concise name available to callers while satisfying the exception
# naming convention used by the repository's Ruff policy.
SmokeFailure = SmokeError


class Reporter:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def pass_(self, label: str) -> None:
        print(f"PASS {label}")

    def fail(self, label: str, detail: str) -> None:
        self.failures.append(f"{label}: {detail}")
        print(f"FAIL {label}: {detail}", file=sys.stderr)


def _json_object(result: CommandResult, label: str) -> object:
    if result.returncode != 0:
        raise SmokeFailure(f"{label} exited {result.returncode}: {_safe_error(result.stderr)}")
    try:
        value = cast(object, json.loads(result.stdout))
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{label} did not return JSON") from exc
    if not isinstance(value, (dict, list)):
        raise SmokeFailure(f"{label} returned non-structured JSON")
    return value


def _safe_error(stderr: str) -> str:
    del stderr
    return "command returned an error"


def _auth_identity(value: object, label: str) -> tuple[object, ...]:
    """Return stable, non-secret auth identity fields for recreate checks."""
    if not isinstance(value, Mapping) or value.get("authenticated") is not True:
        raise SmokeFailure(f"{label} is not authenticated")
    return (
        True,
        value.get("credential_type"),
        value.get("user_id"),
        value.get("email"),
        value.get("name"),
    )


def _cli_args(settings: DockerSmokeSettings, *command: str) -> list[str]:
    args = ["docker", "exec", "-i", settings.container, "enji-guard"]
    if settings.project:
        args.extend(("--project", settings.project))
    args.extend(command)
    return args


def _run_cli(settings: DockerSmokeSettings, command: Sequence[str], runner: CommandRunner) -> CommandResult:
    return runner(_cli_args(settings, *command), timeout=settings.timeout_seconds)


def _status_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.casefold() in {"true", "yes", "1"}:
        return True
    if isinstance(value, str) and value.casefold() in {"false", "no", "0"}:
        return False
    return None


def _status_from_fields(task_lifecycle: str | None, can_read: bool | None) -> str | None:
    if task_lifecycle in {"queued", "running", "failed"}:
        return task_lifecycle
    if can_read is True:
        return "ready"
    if can_read is False:
        return "missing"
    return None


def _status_human_signature(output: str) -> tuple[str, str, dict[str, tuple[str, str, bool | None, str | None]]]:
    repository = ""
    head = ""
    audits: dict[str, tuple[str, str, bool | None, str | None]] = {}
    for line in output.splitlines():
        if line.startswith("repository: "):
            repository = line.removeprefix("repository: ").strip()
        elif line.startswith("current_head: "):
            head = line.removeprefix("current_head: ").strip()
        elif match := re.fullmatch(
            r"  (\S+)\s+state=(\S+)\s+freshness=(\S+)"
            r"(?:\s+can_read=(\S+))?(?:\s+task_lifecycle=(\S+))?",
            line,
        ):
            can_read = _status_bool(match.group(4))
            task_lifecycle = match.group(5)
            audits[match.group(1)] = (match.group(2), match.group(3), can_read, task_lifecycle)
    if not repository or not head or not audits:
        raise SmokeFailure("human status omitted repository, head, or audits")
    return repository, head, audits


def _status_json_item(item: object) -> tuple[str, tuple[str, str, bool | None, str | None]]:
    if not isinstance(item, Mapping) or not isinstance(item.get("audit_key"), str):
        raise SmokeFailure("JSON status contained an invalid audit item")
    freshness = item.get("freshness")
    if not isinstance(freshness, Mapping) or not isinstance(freshness.get("state"), str):
        raise SmokeFailure("JSON status audit omitted freshness")
    can_read = _status_bool(item.get("can_read"))
    task_lifecycle = item.get("task_lifecycle")
    if task_lifecycle is not None and not isinstance(task_lifecycle, str):
        raise SmokeFailure("JSON status audit contained an invalid task lifecycle")
    state_value = item.get("state")
    if state_value is not None and not isinstance(state_value, str):
        raise SmokeFailure("JSON status audit contained an invalid lifecycle state")
    derived_state = _status_from_fields(task_lifecycle, can_read)
    if state_value is not None and derived_state is not None and state_value != derived_state:
        raise SmokeFailure("JSON status lifecycle fields disagree")
    state = state_value or derived_state
    if state is None:
        raise SmokeFailure("JSON status audit omitted lifecycle state")
    key = item["audit_key"].removeprefix("audit.")
    return key, (state, freshness["state"], can_read, task_lifecycle)


def _status_json_signature(value: object) -> tuple[str, str, dict[str, tuple[str, str, bool | None, str | None]]]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        raise SmokeFailure("JSON status did not return exactly one repository")
    repository = value[0].get("repository")
    audit = value[0].get("audit")
    if not isinstance(repository, Mapping) or not isinstance(audit, Mapping):
        raise SmokeFailure("JSON status omitted repository or audit")
    summary = audit.get("summary")
    if not isinstance(summary, Mapping) or not isinstance(summary.get("items"), list):
        raise SmokeFailure("JSON status omitted audit summary")
    audits = dict(_status_json_item(item) for item in summary["items"])
    full_name = repository.get("full_name")
    head = summary.get("current_head_sha")
    if not isinstance(full_name, str) or not isinstance(head, str) or not audits:
        raise SmokeFailure("JSON status omitted repository, head, or audits")
    return full_name, head, audits


def _check_cli(settings: DockerSmokeSettings, runner: CommandRunner, reporter: Reporter) -> None:
    checks: tuple[tuple[str, tuple[str, ...], bool], ...] = (
        ("cli help", ("--help",), False),
        ("cli auth status", ("auth", "status", "--json"), True),
        ("cli readiness", ("health", "--ready", "--json"), True),
    )
    _check_cli_basics(settings, runner, reporter, checks)

    human = _run_cli(settings, ("status", settings.repo), runner)
    structured = _run_cli(settings, ("status", settings.repo, "--json"), runner)
    if human.returncode != 0 or structured.returncode != 0:
        raise SmokeFailure("status human or JSON command returned an error")
    human_signature = _status_human_signature(human.stdout)
    json_signature = _status_json_signature(_json_object(structured, "cli status json"))
    if human_signature[:2] != json_signature[:2]:
        raise SmokeFailure("status human and JSON repository signatures differ")
    human_audits = human_signature[2]
    json_audits = json_signature[2]
    if set(human_audits) != set(json_audits):
        raise SmokeFailure("status human and JSON audit sets differ")
    for audit_key, human_fields in human_audits.items():
        json_fields = json_audits[audit_key]
        if human_fields[0:2] != json_fields[0:2]:
            raise SmokeFailure("status human and JSON lifecycle/freshness differ")
        if human_fields[2] is not None and human_fields[2] != json_fields[2]:
            raise SmokeFailure("status human and JSON can_read differs")
        if human_fields[3] is not None and human_fields[3] != json_fields[3]:
            raise SmokeFailure("status human and JSON task_lifecycle differs")
    reporter.pass_("cli status human/JSON parity")

    validation = _run_cli(settings, ("status", "--sort", "__release_smoke_invalid__"), runner)
    if validation.returncode == 0 or "sort must be one of" not in validation.stderr:
        raise SmokeFailure("validation accepted an invalid sort")
    reporter.pass_("cli validation")


def _check_cli_basics(
    settings: DockerSmokeSettings,
    runner: CommandRunner,
    reporter: Reporter,
    checks: tuple[tuple[str, tuple[str, ...], bool], ...],
) -> None:
    for label, command, structured in checks:
        result = _run_cli(settings, command, runner)
        if result.returncode != 0:
            raise SmokeFailure(f"{label} exited {result.returncode}: {_safe_error(result.stderr)}")
        if structured:
            _json_object(result, label)
        elif not result.stdout.strip():
            raise SmokeFailure(f"{label} returned empty output")
        reporter.pass_(label)


def _rpc_payload(response: McpResponse) -> dict[str, object]:
    body = response.body.strip()
    if body.startswith("{"):
        value = cast(object, json.loads(body))
    else:
        events = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        if not events:
            raise SmokeFailure("MCP returned no JSON-RPC event")
        value = cast(object, json.loads(events[-1]))
    if not isinstance(value, dict):
        raise SmokeFailure("MCP returned a non-object JSON-RPC payload")
    error = value.get("error")
    if isinstance(error, dict):
        raise SmokeFailure("MCP returned a JSON-RPC error")
    return cast(dict[str, object], value)


def _mcp_content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_mcp_content_text(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(_mcp_content_text(item) for item in value)
    return ""


def _mcp_request_payload(
    method: str, params: Mapping[str, object], request_id: int
) -> tuple[dict[str, object], int, bool]:
    is_notification = method.startswith("notifications/")
    payload: dict[str, object] = {"jsonrpc": "2.0", "method": method, "params": dict(params)}
    if not is_notification:
        request_id += 1
        payload["id"] = request_id
    return payload, request_id, is_notification


def _mcp_tool_calls(
    request: Callable[[str, Mapping[str, object]], dict[str, object]],
    settings: DockerSmokeSettings,
    reporter: Reporter,
) -> None:
    for name, arguments in (
        ("enji_portfolio_overview", {"project": settings.project or ""}),
        ("enji_repo_audits", {"repo": settings.repo, "project": settings.project or ""}),
    ):
        result = request("tools/call", {"name": name, "arguments": arguments}).get("result")
        if not isinstance(result, dict):
            raise SmokeFailure(f"MCP {name} returned no result")
        if result.get("isError") is True:
            raise SmokeFailure(f"MCP {name} returned an error result")
        content = result.get("content")
        if not isinstance(content, list) or not content or not _mcp_content_text(content).strip():
            raise SmokeFailure(f"MCP {name} returned missing or empty content")
        reporter.pass_(f"mcp call {name}")


def _mcp_check(
    settings: DockerSmokeSettings, reporter: Reporter, transport_factory: object = http_mcp_transport
) -> None:
    if not callable(transport_factory):
        raise TypeError("transport_factory must be callable")
    transport = cast(
        McpTransport,
        lambda payload, headers: cast(
            McpResponse, transport_factory(payload, headers, url=settings.mcp_url, timeout=settings.timeout_seconds)
        ),
    )
    session_headers: dict[str, str] = {}
    request_id = 0

    def request(method: str, params: Mapping[str, object]) -> dict[str, object]:
        nonlocal request_id, session_headers
        payload, request_id, is_notification = _mcp_request_payload(method, params, request_id)
        response = transport(payload, session_headers)
        session_id = next((value for key, value in response.headers.items() if key.lower() == "mcp-session-id"), None)
        if session_id:
            session_headers = {**session_headers, "Mcp-Session-Id": session_id}
        if is_notification and not response.body.strip():
            return {}
        return _rpc_payload(response)

    initialize = request(
        "initialize",
        {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "release-smoke", "version": "1"}},
    )
    if not isinstance(initialize.get("result"), dict):
        raise SmokeFailure("MCP initialize returned no result")
    request("notifications/initialized", {})
    listed = request("tools/list", {}).get("result")
    if not isinstance(listed, dict) or not isinstance(listed.get("tools"), list):
        raise SmokeFailure("MCP tools/list returned no tools")
    names = {tool.get("name") for tool in listed["tools"] if isinstance(tool, dict)}
    expected = {"enji_portfolio_overview", "enji_repo_audits"}
    if names != expected:
        raise SmokeFailure(f"MCP tool set mismatch: {sorted(str(name) for name in names)}")
    reporter.pass_("mcp exact read-only tools")
    _mcp_tool_calls(request, settings, reporter)


def run_probe(
    settings: DockerSmokeSettings,
    *,
    runner: CommandRunner = subprocess_runner,
    transport_factory: object = http_mcp_transport,
    reporter: Reporter | None = None,
) -> tuple[str, ...]:
    """Run one bounded health/status/MCP probe; used by smoke and soak."""
    output = reporter or Reporter()
    health = runner(
        ["docker", "inspect", "--format={{.State.Health.Status}}", settings.container], timeout=settings.timeout_seconds
    )
    if health.returncode != 0 or health.stdout.strip() != "healthy":
        raise SmokeFailure(f"docker health is not healthy: {_safe_error(health.stderr or health.stdout)}")
    output.pass_("docker health")
    _check_cli(settings, runner, output)
    _mcp_check(settings, output, transport_factory)
    return tuple(output.failures)


def run_smoke(
    settings: DockerSmokeSettings,
    *,
    runner: CommandRunner = subprocess_runner,
    transport_factory: object = http_mcp_transport,
) -> int:
    reporter = Reporter()
    try:
        if settings.recreate:
            before = _run_cli(settings, ("auth", "status", "--json"), runner) if settings.auth_persistence else None
            recreate = runner(
                [
                    "docker",
                    "compose",
                    "-f",
                    settings.compose_file,
                    "up",
                    "-d",
                    "--force-recreate",
                    "--wait",
                    settings.container,
                ],
                timeout=max(settings.timeout_seconds, 90),
            )
            if recreate.returncode != 0:
                raise SmokeFailure(f"docker recreate failed: {_safe_error(recreate.stderr)}")
            reporter.pass_("docker recreate")
            if before is not None:
                after = _run_cli(settings, ("auth", "status", "--json"), runner)
                before_identity = _auth_identity(
                    _json_object(before, "auth status before recreate"), "auth status before recreate"
                )
                after_identity = _auth_identity(
                    _json_object(after, "auth status after recreate"), "auth status after recreate"
                )
                if before_identity != after_identity:
                    raise SmokeFailure("authenticated identity changed across recreate")
                reporter.pass_("auth persistence")
        run_probe(settings, runner=runner, transport_factory=transport_factory, reporter=reporter)
    except (OSError, SmokeFailure, ValueError) as exc:
        reporter.fail("release smoke", str(exc))
    return EXIT_ASSERTION if reporter.failures else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Repository owner/name used for read-only probes.")
    parser.add_argument("--project", help="Exact project selector.")
    parser.add_argument("--container", default="enji-guard-cli")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--compose-file", default="docker-compose.yml")
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--auth-persistence", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    timeout = cast(float, args.timeout)
    auth_persistence = cast(bool, args.auth_persistence)
    recreate = cast(bool, args.recreate)
    if timeout <= 0:
        print("FAIL configuration: --timeout must be positive", file=sys.stderr)
        return EXIT_CONFIG
    if auth_persistence and not recreate:
        print("FAIL configuration: --auth-persistence requires --recreate", file=sys.stderr)
        return EXIT_CONFIG
    settings = DockerSmokeSettings(
        repo=cast(str, args.repo),
        project=cast(str | None, args.project),
        container=cast(str, args.container),
        mcp_url=cast(str, args.mcp_url),
        timeout_seconds=timeout,
        recreate=recreate,
        auth_persistence=auth_persistence,
        compose_file=cast(str, args.compose_file),
    )
    return run_smoke(settings)


if __name__ == "__main__":
    raise SystemExit(main())
