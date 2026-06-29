import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, TypeGuard

import typer

from enji_guard_cli.auth import AuthError, AuthStatusPayload, import_bearer_token, import_cookie, refresh_auth
from enji_guard_cli.core import (
    REPORTS_LIST_DEFAULT_SELECTOR,
    AuditAlias,
    OperationName,
    OperationResult,
    package_version,
    resolve_operation_result,
    resolve_operation_spec,
)
from enji_guard_cli.enji_api import EnjiApiError
from enji_guard_cli.mcp_server import create_mcp_server, run_mcp_server
from enji_guard_cli.telemetry import configure_logging

app = typer.Typer(help="Enji Guard command-line tools.")
catalog_app = typer.Typer(help="Local Enji Guard catalog metadata.")
auth_app = typer.Typer(help="Authentication and session-cookie management.")
report_app = typer.Typer(help="Enji Guard report surfaces.")
app.add_typer(catalog_app, name="catalog")
app.add_typer(auth_app, name="auth")
app.add_typer(report_app, name="report")

CATALOG_AUDITS_OPERATION = resolve_operation_spec(OperationName.CATALOG_AUDITS)
CATALOG_AUDIT_OPERATION = resolve_operation_spec(OperationName.CATALOG_AUDIT)
ACCESS_OPERATION = resolve_operation_spec(OperationName.ACCESS)
REPORTS_LIST_OPERATION = resolve_operation_spec(OperationName.REPORTS_LIST)
AUTH_STATUS_OPERATION = resolve_operation_spec(OperationName.AUTH_STATUS)

get_access = ACCESS_OPERATION.execute
get_reports_list = REPORTS_LIST_OPERATION.execute
auth_status = AUTH_STATUS_OPERATION.execute

type JsonCommandAction = Callable[[], OperationResult]


def _echo_json(payload: object, pretty: bool) -> None:
    indent = 2 if pretty else None
    typer.echo(json.dumps(payload, indent=indent, sort_keys=True))


def _echo_error(code: str, message: str) -> None:
    typer.echo(json.dumps({"code": code, "message": message}, sort_keys=True), err=True)


def _run_json_command(action: JsonCommandAction, pretty: bool) -> None:
    try:
        _echo_json(resolve_operation_result(action()), pretty)
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    except EnjiApiError as exc:
        _echo_error(exc.code, exc.message)
        raise typer.Exit(_exit_code_for_error(exc.code)) from None


def _exit_code_for_error(code: str) -> int:
    if code.startswith("AUTH_"):
        return 3
    if code in {"NOT_FOUND", "BAD_SELECTOR"}:
        return 4
    return 1


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[bool, typer.Option("--version", help="Show the installed version and exit.")] = False,
    log_level: Annotated[
        str | None,
        typer.Option("--log-level", help="Project log level. Defaults to ENJI_GUARD_LOG_LEVEL or WARNING."),
    ] = None,
    log_format: Annotated[
        Literal["text", "json"] | None,
        typer.Option("--log-format", help="Project log format. Defaults to ENJI_GUARD_LOG_FORMAT or text."),
    ] = None,
) -> None:
    configure_logging(log_level, log_format)
    if version:
        typer.echo(package_version())
        raise typer.Exit


@app.command()
def health() -> None:
    typer.echo("ok")


@app.command(help=ACCESS_OPERATION.summary)
def access(
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(get_access, pretty)


@app.command()
def serve(
    transport: Annotated[
        Literal["stdio", "sse", "streamable-http"],
        typer.Option(help="FastMCP transport to run."),
    ] = "stdio",
    host: Annotated[str, typer.Option(help="Host for HTTP MCP transports.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port for HTTP MCP transports.")] = 8000,
    mount_path: Annotated[
        str | None,
        typer.Option(help="Optional mount path for SSE transport."),
    ] = None,
) -> None:
    run_mcp_server(create_mcp_server(host=host, port=port), transport=transport, mount_path=mount_path)


@report_app.command("list", help=REPORTS_LIST_OPERATION.summary)
def report_list(
    selector: Annotated[
        str,
        typer.Option("--selector", help="Repository selector. Defaults to '*'."),
    ] = REPORTS_LIST_DEFAULT_SELECTOR,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(lambda: get_reports_list(selector=selector), pretty)


@catalog_app.command("audits", help=CATALOG_AUDITS_OPERATION.summary)
def catalog_audits(
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _echo_json(resolve_operation_result(CATALOG_AUDITS_OPERATION.execute()), pretty)


@catalog_app.command("audit", help=CATALOG_AUDIT_OPERATION.summary)
def catalog_audit(
    audit: Annotated[AuditAlias, typer.Argument(help="Canonical audit alias.")],
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _echo_json(resolve_operation_result(CATALOG_AUDIT_OPERATION.execute(audit)), pretty)


@auth_app.command("import-cookie")
def auth_import_cookie(
    stdin: Annotated[bool, typer.Option("--stdin", help="Read a raw Cookie header from stdin.")] = False,
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ENJI_GUARD_AUTH_FILE or XDG config."),
    ] = None,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    if not stdin:
        _echo_error("VALIDATION", "use --stdin to avoid storing cookies in shell history")
        raise typer.Exit(1)

    raw_cookie = sys.stdin.read()
    try:
        _echo_json(import_cookie(raw_cookie, auth_file), pretty)
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    except OSError as exc:
        _echo_error("STORAGE", str(exc))
        raise typer.Exit(1) from None


@auth_app.command("import-token")
def auth_import_token(
    stdin: Annotated[bool, typer.Option("--stdin", help="Read a bearer token from stdin.")] = False,
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ENJI_GUARD_AUTH_FILE or XDG config."),
    ] = None,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    if not stdin:
        _echo_error("VALIDATION", "use --stdin to avoid storing tokens in shell history")
        raise typer.Exit(1)

    raw_token = sys.stdin.read()
    try:
        _echo_json(import_bearer_token(raw_token, auth_file), pretty)
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    except OSError as exc:
        _echo_error("STORAGE", str(exc))
        raise typer.Exit(1) from None


@auth_app.command("status", help=AUTH_STATUS_OPERATION.summary)
def auth_status_command(
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ENJI_GUARD_AUTH_FILE or XDG config."),
    ] = None,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    payload = cast_auth_status_payload(resolve_operation_result(auth_status(auth_file)))
    _echo_json(payload, pretty)
    if not payload["authenticated"]:
        raise typer.Exit(3)


@auth_app.command("refresh")
def auth_refresh_command(
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ENJI_GUARD_AUTH_FILE or XDG config."),
    ] = None,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    try:
        _echo_json(refresh_auth(auth_file), pretty)
    except AuthError as exc:
        _echo_error(exc.code, exc.message)
        raise typer.Exit(_exit_code_for_error(exc.code)) from None


def cast_auth_status_payload(payload: object) -> AuthStatusPayload:
    return payload if _is_auth_status_payload(payload) else _invalid_auth_status_payload()


def _is_auth_status_payload(payload: object) -> TypeGuard[AuthStatusPayload]:
    return isinstance(payload, dict) and isinstance(payload.get("authenticated"), bool)


def _invalid_auth_status_payload() -> AuthStatusPayload:
    return {
        "authenticated": False,
        "code": "UPSTREAM",
        "message": "auth status returned unexpected payload",
        "auth_file": "",
        "credential_type": None,
        "email": None,
        "name": None,
        "user_id": None,
    }
