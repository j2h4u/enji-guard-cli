"""Dedicated root for the optional long-lived MCP service."""

from pathlib import Path
from typing import Annotated, Literal, Protocol

import typer

from enji_guard_cli.runtime_observability.supervisor import (
    McpServerRunner,
    RuntimeServiceOptions,
    run_service,
)
from enji_guard_cli.service_composition import runtime_auth_service
from enji_guard_cli.settings import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT, DEFAULT_MCP_TRANSPORT, default_settings

MCP_EXTRA_REQUIRED = "MCP_EXTRA_REQUIRED"

app = typer.Typer(help="Run the optional long-lived Enji Guard MCP service.", invoke_without_command=True)


class McpServerBuilder(Protocol):
    """The optional MCP implementation's construction seam."""

    def __call__(self, host: str, port: int, *, auth_file: Path | None = None) -> object: ...


def _validate_http_bind(host: str, transport: str, *, allow_external_host: bool) -> None:
    if transport == "stdio" or allow_external_host or host.strip().lower() in {"localhost", "127.0.0.1", "::1"}:
        return
    raise typer.BadParameter(
        "HTTP MCP transports may only bind to loopback by default; pass --allow-external-host to bind externally",
        param_hint="--host",
    )


def _mcp_implementation() -> tuple[McpServerBuilder, McpServerRunner]:
    """Load the optional MCP implementation without hiding its own failures."""
    try:
        from enji_guard_cli.delivery.mcp.server import create_mcp_server, run_mcp_server_async
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise
        raise EnjiGuardMcpExtraRequiredError from None
    return create_mcp_server, run_mcp_server_async


class EnjiGuardMcpExtraRequiredError(Exception):
    """The base package is installed without its optional MCP dependency."""


def run(
    options: RuntimeServiceOptions,
    *,
    allow_external_host: bool = False,
    auth_file: Path | None = None,
) -> None:
    """Run the service, loading MCP only at the point it is actually needed."""
    _validate_http_bind(options.host, options.transport, allow_external_host=allow_external_host)
    try:
        create_mcp_server, run_mcp_server_async = _mcp_implementation()
    except EnjiGuardMcpExtraRequiredError:
        typer.echo(f"{MCP_EXTRA_REQUIRED}: install 'enji-guard-cli[mcp]' to run the MCP service", err=True)
        raise typer.Exit(2) from None

    def server_factory(host: str, port: int) -> object:
        return create_mcp_server(host, port, auth_file=auth_file)

    with runtime_auth_service(auth_file) as runtime_auth:
        run_service(
            options=options,
            runtime_auth=runtime_auth,
            mcp_server_factory=server_factory,
            mcp_server_runner=run_mcp_server_async,
            settings=default_settings(),
        )


@app.callback()
def service(
    *,
    transport: Annotated[
        Literal["stdio", "sse", "streamable-http"], typer.Option("--transport")
    ] = DEFAULT_MCP_TRANSPORT,
    host: Annotated[str, typer.Option("--host")] = DEFAULT_HTTP_HOST,
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = DEFAULT_HTTP_PORT,
    mount_path: Annotated[str | None, typer.Option("--mount-path")] = None,
    allow_external_host: Annotated[bool, typer.Option("--allow-external-host")] = False,
) -> None:
    run(
        RuntimeServiceOptions(transport=transport, host=host, port=port, mount_path=mount_path),
        allow_external_host=allow_external_host,
    )


__all__ = ["MCP_EXTRA_REQUIRED", "RuntimeServiceOptions", "app", "run", "service"]
