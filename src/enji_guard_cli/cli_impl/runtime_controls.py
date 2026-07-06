import socket
from ipaddress import IPv6Address, ip_address
from typing import Literal

import typer

from enji_guard_cli.mcp_server import create_mcp_server, run_mcp_server
from enji_guard_cli.readiness import readiness_verdict
from enji_guard_cli.runtime import run_service
from enji_guard_cli.settings import default_settings

ANY_IPV4_HOST = str(ip_address(0))
ANY_IPV6_HOST = str(IPv6Address(0))
LOCALHOST_NAME = "localhost"


def _echo_error(code: str, message: str) -> None:
    typer.echo(f"{code}: {message}", err=True)


def _check_local_listener(host: str, port: int) -> None:
    with socket.create_connection(
        (host, port),
        timeout=default_settings().service.local_readiness_timeout_seconds,
    ):
        pass


def _check_backend_readiness() -> None:
    verdict = readiness_verdict()
    if verdict.ready:
        return
    reason = verdict.reason if verdict.reason is not None else "backend readiness failed"
    state = verdict.state
    if state is not None and state.failure_code is not None:
        reason = f"{reason}: {state.failure_code}"
    _echo_error("UNREADY", reason)
    raise typer.Exit(1) from None


def _validate_http_bind(host: str, transport: str, *, allow_external_host: bool) -> None:
    if transport == "stdio" or allow_external_host or _is_loopback_host(host):
        return
    _echo_error(
        "VALIDATION",
        "HTTP MCP transports may only bind to loopback by default; pass --allow-external-host to bind externally",
    )
    raise typer.Exit(1)


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == LOCALHOST_NAME:
        return True
    if normalized in {ANY_IPV4_HOST, ANY_IPV6_HOST}:
        return False
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _run_service_body(
    transport: Literal["stdio", "sse", "streamable-http"],
    host: str,
    port: int,
    mount_path: str | None,
    allow_external_host: bool,
) -> object:
    _validate_http_bind(host, transport, allow_external_host=allow_external_host)
    run_service(transport=transport, host=host, port=port, mount_path=mount_path)
    return None


def _serve_body(
    transport: Literal["stdio", "sse", "streamable-http"],
    host: str,
    port: int,
    mount_path: str | None,
    allow_external_host: bool,
) -> object:
    _validate_http_bind(host, transport, allow_external_host=allow_external_host)
    run_mcp_server(create_mcp_server(host=host, port=port), transport=transport, mount_path=mount_path)
    return None
