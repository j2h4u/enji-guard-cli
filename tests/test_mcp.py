import asyncio
from pathlib import Path
from typing import cast

import pytest

pytest.importorskip("mcp.server.fastmcp")

from mcp.server.fastmcp import FastMCP
from mcp.types import Tool

import enji_guard_cli.mcp_server as mcp_server
from enji_guard_cli.audits import AuditAlias, AuditPayload
from enji_guard_cli.auth import AuthStatusPayload
from enji_guard_cli.mcp_server import create_mcp_server


def call_structured_tool(server: FastMCP, name: str, arguments: dict[str, object]) -> object:
    _text, structured = cast(tuple[object, object], asyncio.run(server.call_tool(name, arguments)))
    return structured


def test_create_mcp_server_registers_expected_tools() -> None:
    server = create_mcp_server()

    tools = cast(list[Tool], asyncio.run(server.list_tools()))
    names = {tool.name for tool in tools}

    assert names == {
        "enji_catalog_audits",
        "enji_catalog_audit",
        "enji_access",
        "enji_reports_list",
        "enji_auth_status",
    }


def test_run_mcp_server_async_runs_streamable_http_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_mcp_server()
    called = False

    async def fake_run_streamable_http_async() -> None:
        nonlocal called

        called = True

    monkeypatch.setattr(server, "run_streamable_http_async", fake_run_streamable_http_async)

    asyncio.run(mcp_server.run_mcp_server_async(server, transport="streamable-http"))

    assert called is True


def test_catalog_audit_tool_uses_audit_alias_enum_schema() -> None:
    server = create_mcp_server()

    tools = cast(list[Tool], asyncio.run(server.list_tools()))
    catalog_audit_tool = next(tool for tool in tools if tool.name == "enji_catalog_audit")
    defs = cast(dict[str, dict[str, object]], catalog_audit_tool.inputSchema["$defs"])

    assert defs["AuditAlias"]["enum"] == [
        "security",
        "ai-readiness",
        "tests",
        "tech-health",
        "deps",
        "cognitive-debt",
        "dead-code",
        "recon",
    ]


def test_catalog_audits_tool_returns_catalog_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_mcp_server()
    payload: list[AuditPayload] = [
        {
            "alias": "security",
            "label": "Security",
            "route_slug": "vulns",
            "job_kind": "vuln-audit",
            "action_key": "audit.security",
        }
    ]

    monkeypatch.setattr(mcp_server, "get_audit_catalog", lambda: payload)

    structured = call_structured_tool(server, "enji_catalog_audits", {})

    assert structured == {"audits": payload}


def test_catalog_audit_tool_resolves_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_mcp_server()
    captured: dict[str, AuditAlias] = {}
    payload: AuditPayload = {
        "alias": "deps",
        "label": "Dependency hygiene",
        "route_slug": "dependency-hygiene",
        "job_kind": "dependency-hygiene",
        "action_key": "audit.dependency-hygiene",
    }

    def fake_resolve_audit(audit: AuditAlias) -> AuditPayload:
        captured["audit"] = audit
        return payload

    monkeypatch.setattr(mcp_server, "get_resolve_audit", fake_resolve_audit)

    structured = call_structured_tool(server, "enji_catalog_audit", {"audit": "deps"})

    assert structured == payload
    assert captured["audit"] is AuditAlias.DEPS


def test_access_tool_returns_access_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_mcp_server()
    payload = {
        "group": "pro",
        "full_access": True,
        "limits": {"can_use_schedules": True, "audit_runs": {}, "autofix_runs": {}},
        "usage": [],
    }

    monkeypatch.setattr(mcp_server, "get_access", lambda: payload)

    structured = call_structured_tool(server, "enji_access", {})

    assert structured == payload


def test_reports_list_tool_passes_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_mcp_server()
    captured: dict[str, object | None] = {}
    payload: dict[str, object] = {
        "projects": [{"id": "project_1", "name": "Pets", "repo_ids": ["repo_1"], "scores": {}}],
    }

    def fake_reports_list(selector: str = "*") -> dict[str, object]:
        captured["selector"] = selector
        return payload

    monkeypatch.setattr(mcp_server, "get_reports_list", fake_reports_list)

    structured = call_structured_tool(
        server,
        "enji_reports_list",
        {"selector": "pets/*"},
    )

    assert structured == payload
    assert captured == {"selector": "pets/*"}


def test_auth_status_tool_passes_optional_auth_file(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_mcp_server()
    captured: dict[str, Path | None] = {}
    payload: AuthStatusPayload = {
        "authenticated": False,
        "code": "AUTH_REQUIRED",
        "message": "auth file does not exist",
        "auth_file": "/tmp/auth.json",
        "credential_type": None,
        "email": None,
        "name": None,
        "user_id": None,
    }

    def fake_auth_status(auth_file: Path | None = None) -> AuthStatusPayload:
        captured["auth_file"] = auth_file
        return payload

    monkeypatch.setattr(mcp_server, "get_auth_status", fake_auth_status)

    structured = call_structured_tool(server, "enji_auth_status", {"auth_file": "~/tmp/auth.json"})

    assert structured == payload
    assert captured["auth_file"] == Path("~/tmp/auth.json").expanduser()
