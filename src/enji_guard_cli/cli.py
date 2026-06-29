import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, TypeGuard

import typer

from enji_guard_cli.auth import AuthError, AuthStatusPayload, import_bearer_token, import_cookie, refresh_auth
from enji_guard_cli.core import (
    DEFAULT_REPO_SORT,
    REPORTS_LIST_DEFAULT_SELECTOR,
    AuditAlias,
    OperationName,
    OperationResult,
    ReportAuditAlias,
    ScheduleUpdate,
    connect_repo,
    current_repo,
    disable_schedule_for_repo,
    list_project_inventory,
    list_projects,
    list_schedules_for_repo,
    package_version,
    read_reports_for_repo,
    resolve_operation_result,
    resolve_operation_spec,
    resolve_repo,
    runtime_status,
    schedule_payload,
    set_schedule_for_repo,
    show_report_for_repo,
    start_recon,
    start_report_audits,
    wait_for_work,
)
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.mcp_server import create_mcp_server, run_mcp_server
from enji_guard_cli.runtime import run_service
from enji_guard_cli.telemetry import configure_logging

app = typer.Typer(help="Enji Guard command-line tools.")
catalog_app = typer.Typer(help="Local Enji Guard catalog metadata.")
auth_app = typer.Typer(help="Authentication and session-cookie management.")
project_app = typer.Typer(help="Project inventory commands.")
repo_app = typer.Typer(help="Repository workflow commands.")
recon_app = typer.Typer(help="Preliminary repository diagnostics.")
audit_app = typer.Typer(help="Report audit commands.")
report_app = typer.Typer(help="Enji Guard report surfaces.")
schedule_app = typer.Typer(help="Audit schedule commands.")
app.add_typer(catalog_app, name="catalog", hidden=True)
app.add_typer(auth_app, name="auth")
app.add_typer(project_app, name="project")
app.add_typer(repo_app, name="repo")
app.add_typer(recon_app, name="recon")
app.add_typer(audit_app, name="audit")
app.add_typer(report_app, name="report")
app.add_typer(schedule_app, name="schedule")

CATALOG_AUDITS_OPERATION = resolve_operation_spec(OperationName.CATALOG_AUDITS)
CATALOG_AUDIT_OPERATION = resolve_operation_spec(OperationName.CATALOG_AUDIT)
ACCESS_OPERATION = resolve_operation_spec(OperationName.ACCESS)
REPORTS_LIST_OPERATION = resolve_operation_spec(OperationName.REPORTS_LIST)
AUTH_STATUS_OPERATION = resolve_operation_spec(OperationName.AUTH_STATUS)

get_access = ACCESS_OPERATION.execute
get_reports_list = REPORTS_LIST_OPERATION.execute
auth_status = AUTH_STATUS_OPERATION.execute
_cli_state: dict[str, str | None] = {"project": None}

type JsonCommandAction = Callable[[], OperationResult]


def _echo_json(payload: object, pretty: bool) -> None:
    indent = 2 if pretty else None
    typer.echo(json.dumps(payload, indent=indent, sort_keys=True))


def _echo_error(code: str, message: str) -> None:
    typer.echo(json.dumps({"code": code, "message": message}, sort_keys=True), err=True)


def _run_json_command(action: JsonCommandAction, pretty: bool) -> None:
    _echo_json(_resolve_command_payload(action), pretty)


def _resolve_command_payload(action: JsonCommandAction) -> object:
    try:
        return resolve_operation_result(action())
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
    project: Annotated[
        str | None,
        typer.Option("--project", help="Global exact Enji project id or name filter."),
    ] = None,
    log_level: Annotated[
        str | None,
        typer.Option("--log-level", help="Project log level. Defaults to ENJI_GUARD_LOG_LEVEL or WARNING."),
    ] = None,
    log_format: Annotated[
        Literal["text", "json"] | None,
        typer.Option("--log-format", help="Project log format. Defaults to ENJI_GUARD_LOG_FORMAT or text."),
    ] = None,
) -> None:
    _cli_state["project"] = project
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
def run(
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
    run_service(transport=transport, host=host, port=port, mount_path=mount_path)


@app.command(hidden=True)
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


@app.command()
def status(
    repo: Annotated[str | None, typer.Argument(help="Repo id or owner/name. Defaults to all repos.")] = None,
    sort: Annotated[
        Literal["default", "name", "weakest", "overall"],
        typer.Option("--sort", help="Sort repos by default order, name, weakest score, or overall score."),
    ] = DEFAULT_REPO_SORT,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(lambda: runtime_status(repo, _selected_project(), sort), pretty)


@app.command()
def wait(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audit: Annotated[AuditAlias, typer.Argument(help="recon or canonical report audit alias.")],
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, help="Maximum wait time in seconds."),
    ] = 7200,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    payload = _resolve_command_payload(
        lambda: wait_for_work(repo, audit, _selected_project(), poll_seconds=10, timeout_seconds=timeout_seconds)
    )
    _echo_json(payload, pretty)
    if isinstance(payload, dict) and payload.get("idle") is False:
        raise typer.Exit(2)


@project_app.command("list")
def project_list(
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(list_projects, pretty)


@report_app.command("list", help=REPORTS_LIST_OPERATION.summary)
def report_list(
    selector: Annotated[
        str,
        typer.Option("--selector", help="Repository selector. Defaults to '*'."),
    ] = REPORTS_LIST_DEFAULT_SELECTOR,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(lambda: get_reports_list(selector=selector), pretty)


@report_app.command("read")
def report_read(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audits: Annotated[
        list[ReportAuditAlias] | None,
        typer.Argument(help="Optional report audit aliases. Defaults to ready reports."),
    ] = None,
    all_reports: Annotated[bool, typer.Option("--all", help="Read every report audit.")] = False,
    output_format: Annotated[
        Literal["markdown", "json"],
        typer.Option("--format", help="Output markdown reports or JSON snapshots."),
    ] = "markdown",
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    payload = _resolve_command_payload(
        lambda: read_reports_for_repo(repo, _selected_project(), _report_audits(audits or []), all_reports=all_reports)
    )
    if output_format == "json":
        _echo_json(payload, pretty)
        return
    try:
        typer.echo(_reports_markdown(payload))
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None


@report_app.command("show")
def report_show(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audit: Annotated[ReportAuditAlias, typer.Argument(help="Canonical report audit alias.")],
    output_format: Annotated[
        Literal["json", "markdown"],
        typer.Option("--format", help="Output JSON snapshot or markdown report."),
    ] = "json",
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    payload = _resolve_command_payload(lambda: show_report_for_repo(repo, _report_audit(audit), _selected_project()))
    if output_format == "json":
        _echo_json(payload, pretty)
        return
    try:
        typer.echo(_report_markdown(payload))
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None


@repo_app.command("current")
def repo_current(
    path: Annotated[
        Path | None,
        typer.Option("--path", help="Path inside the local Git repository. Defaults to cwd."),
    ] = None,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _echo_json(current_repo(path), pretty)


@repo_app.command("list")
def repo_list(
    sort: Annotated[
        Literal["default", "name", "weakest", "overall"],
        typer.Option("--sort", help="Sort repos by default order, name, weakest score, or overall score."),
    ] = DEFAULT_REPO_SORT,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(lambda: list_project_inventory(_selected_project(), sort), pretty)


@repo_app.command("resolve")
def repo_resolve(
    repo: Annotated[str | None, typer.Argument(help="Repo id or owner/name. Defaults to current Git repo.")] = None,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(lambda: resolve_repo(repo, _selected_project()), pretty)


@repo_app.command("connect")
def repo_connect(
    github_repo: Annotated[str, typer.Argument(help="GitHub owner/name repository slug.")],
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(lambda: connect_repo(github_repo, _selected_project()), pretty)


@recon_app.command("start")
def recon_start(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(lambda: start_recon(repo, _selected_project()), pretty)


@audit_app.command("start")
def audit_start(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audits: Annotated[
        list[ReportAuditAlias] | None,
        typer.Argument(help="One or more canonical report audit aliases. Use --all for all report audits."),
    ] = None,
    all_reports: Annotated[bool, typer.Option("--all", help="Start every report audit.")] = False,
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(
        lambda: start_report_audits(repo, _selected_project(), _report_audits(audits or []), all_reports=all_reports),
        pretty,
    )


@schedule_app.command("list")
def schedule_list(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(lambda: list_schedules_for_repo(repo, _selected_project()), pretty)


@schedule_app.command("set")
def schedule_set(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audit: Annotated[ReportAuditAlias, typer.Argument(help="Canonical report audit alias.")],
    frequency: Annotated[
        Literal["daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"],
        typer.Option("--freq", help="Schedule frequency."),
    ],
    days: Annotated[
        list[str] | None,
        typer.Option("--day", help="Repeatable day: mon,tue,wed,thu,fri,sat,sun."),
    ] = None,
    at: Annotated[str, typer.Option("--at", help="auto, HH:MM, or HH:MM@TZ.")] = "auto",
) -> None:
    _run_json_command(
        lambda: set_schedule_for_repo(
            repo,
            _report_audit(audit),
            _selected_project(),
            schedule_payload(
                ScheduleUpdate(
                    enabled=True,
                    auto_fix=False,
                    frequency=frequency,
                    days_of_week=_schedule_days(frequency, days),
                    schedule_time=_schedule_time(at),
                    timezone=_schedule_timezone(at),
                )
            ),
        ),
        False,
    )


@schedule_app.command("disable")
def schedule_disable(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audit: Annotated[ReportAuditAlias, typer.Argument(help="Canonical report audit alias.")],
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    _run_json_command(
        lambda: disable_schedule_for_repo(repo, _report_audit(audit), _selected_project()),
        pretty,
    )


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


def _report_markdown(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("report payload is not an object")
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("report payload does not contain snapshot")
    content = snapshot.get("content")
    if not isinstance(content, dict):
        raise ValueError("report snapshot does not contain content")
    report = content.get("report")
    if not isinstance(report, str):
        raise ValueError("report snapshot does not contain markdown report")
    return report


def _reports_markdown(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("reports payload is not an object")
    reports = payload.get("reports")
    if not isinstance(reports, list):
        raise ValueError("reports payload does not contain reports")
    parts = [_report_item_markdown(item) for item in reports]
    return "\n\n---\n\n".join(parts)


def _report_item_markdown(item: object) -> str:
    if not isinstance(item, dict):
        raise ValueError("report item is not an object")
    audit = item.get("audit")
    if not isinstance(audit, str):
        raise ValueError("report item does not contain audit")
    return f"<!-- enji-report audit={audit} -->\n\n{_report_markdown(item).strip()}"


def _schedule_days(frequency: str, days: list[str] | None) -> list[str]:
    if days is not None:
        return days
    if frequency == "daily":
        return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return ["mon", "tue", "wed", "thu", "fri"]


def _schedule_time(at: str) -> str:
    return at.split("@", 1)[0]


def _schedule_timezone(at: str) -> str:
    parts = at.split("@", 1)
    if len(parts) == 1:
        return "UTC"
    return parts[1]


def _selected_project() -> str | None:
    return _cli_state["project"]


def _report_audit(audit: ReportAuditAlias) -> AuditAlias:
    return AuditAlias(audit.value)


def _report_audits(audits: list[ReportAuditAlias]) -> list[AuditAlias]:
    return [_report_audit(audit) for audit in audits]
