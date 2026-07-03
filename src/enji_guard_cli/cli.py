import socket
import sys
from collections.abc import Callable
from ipaddress import IPv6Address, ip_address
from pathlib import Path
from typing import Annotated, Literal, TypeGuard, cast

import typer

from enji_guard_cli.audits import AuditAlias, ReportAuditAlias
from enji_guard_cli.cli_impl.durations import parse_duration_seconds
from enji_guard_cli.cli_impl.rendering import (
    echo_access,
    echo_audit_catalog,
    echo_auth_status,
    echo_email_preferences_table,
    echo_generic_payload,
    echo_json,
    echo_key_values,
    echo_project_table,
    echo_repo_resolve_table,
    echo_repo_score_table,
    echo_repo_status_table,
    echo_schedule_settings_table,
    echo_wait_heartbeat,
    echo_wait_status,
)
from enji_guard_cli.cli_impl.rendering_support import object_dict
from enji_guard_cli.cli_impl.report_rendering import report_read_summary_payload, reports_markdown
from enji_guard_cli.cli_impl.write_targets import (
    parse_email_set_args,
    parse_schedule_set_args,
)
from enji_guard_cli.core import (
    AuthError,
    AuthStatusPayload,
    EmailPreferenceUpdate,
    OperationName,
    OperationResult,
    ReportWaitOptions,
    ScheduleSettingsUpdate,
    connect_repo,
    create_project,
    delete_project,
    import_bearer_token,
    import_cookie,
    list_email_preferences,
    list_project_inventory,
    list_projects,
    list_schedule_settings,
    move_repo,
    package_version,
    read_reports_for_repo,
    refresh_auth,
    rename_project,
    resolve_operation_result,
    resolve_operation_spec,
    resolve_repo,
    runtime_status,
    set_email_preferences,
    set_schedule_settings,
    start_recon,
    start_report_audits,
    wait_for_reports,
)
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.mcp_server import create_mcp_server, run_mcp_server
from enji_guard_cli.readiness import readiness_verdict
from enji_guard_cli.runtime import run_service
from enji_guard_cli.settings import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT, DEFAULT_MCP_TRANSPORT, default_settings
from enji_guard_cli.telemetry import configure_logging

MAIN_HELP = """Agent-oriented CLI for Enji Guard repository audits.

Model: projects group GitHub repositories. Pass the known owner/name repo
selector directly when an agent is working on a specific checkout. Recon is
baseline discovery; report audits are separate slow jobs that produce scores
and readable reports. Use status/list for triage, audit start for work,
wait/status for long-running jobs, and report read for the Markdown findings.
Text tables are the default; add --json for automation.
"""

app = typer.Typer(help=MAIN_HELP)
catalog_app = typer.Typer(help="Local audit aliases and metadata.")
auth_app = typer.Typer(help="Credential bootstrap, refresh, and status.")
project_app = typer.Typer(help="List and manage Enji projects.")
repo_app = typer.Typer(help="Discover, resolve, connect, and move GitHub repositories.")
recon_app = typer.Typer(help="Start baseline discovery. Recon is not a report audit.")
audit_app = typer.Typer(help="Start slow report-producing audits.")
report_app = typer.Typer(help="Read generated audit reports.")
schedule_app = typer.Typer(help="Manage scheduled report audits.")
email_app = typer.Typer(help="Manage report completion email preferences.")
app.add_typer(catalog_app, name="catalog", hidden=True)
app.add_typer(auth_app, name="auth")
app.add_typer(project_app, name="project")
app.add_typer(repo_app, name="repo")
app.add_typer(recon_app, name="recon")
app.add_typer(audit_app, name="audit")
app.add_typer(report_app, name="report")
app.add_typer(schedule_app, name="schedule")
app.add_typer(email_app, name="email")

CATALOG_AUDITS_OPERATION = resolve_operation_spec(OperationName.CATALOG_AUDITS)
CATALOG_AUDIT_OPERATION = resolve_operation_spec(OperationName.CATALOG_AUDIT)
ACCESS_OPERATION = resolve_operation_spec(OperationName.ACCESS)
AUTH_STATUS_OPERATION = resolve_operation_spec(OperationName.AUTH_STATUS)

get_access = ACCESS_OPERATION.execute
auth_status = AUTH_STATUS_OPERATION.execute
_cli_state: dict[str, object] = {"project": None, "json": False}
_DEFAULT_CLI_SETTINGS = default_settings()

type JsonCommandAction = Callable[[], OperationResult]
ANY_IPV4_HOST = str(ip_address(0))
ANY_IPV6_HOST = str(IPv6Address(0))
LOCALHOST_NAME = "localhost"
SCHEDULE_SET_EPILOG = """
Targets: REPO, --project PROJECT --all-repos, or --all-projects.
Options: --enabled on|off, --frequency daily|workdays|weekly-3x|weekly-2x|weekly|monthly, --timezone TZ, --json.
"""
EMAIL_SET_EPILOG = """
Targets: REPO, --project PROJECT --all-repos, or --all-projects.
Options: --manual on|off, --scheduled on|off, --json.
"""


def _echo_error(code: str, message: str) -> None:
    typer.echo(f"{code}: {message}", err=True)


def _run_human_or_json_command(
    action: JsonCommandAction,
    json_output: bool,
    human_renderer: Callable[[object], None] | None = None,
) -> None:
    payload = _resolve_command_payload(action)
    if json_output:
        echo_json(payload)
        return
    renderer = human_renderer if human_renderer is not None else echo_generic_payload
    renderer(payload)


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
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _cli_state["project"] = project
    _cli_state["json"] = json_output
    configure_logging()
    if version:
        typer.echo(package_version())
        raise typer.Exit


@app.command(help="Return process liveness or full service readiness.")
def health(
    ready: Annotated[
        bool,
        typer.Option("--ready", help="Also check local MCP and cached Enji backend readiness."),
    ] = False,
) -> None:
    if ready:
        try:
            _check_local_listener(DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT)
        except OSError as exc:
            _echo_error("UNREADY", f"MCP listener is not ready at {DEFAULT_HTTP_HOST}:{DEFAULT_HTTP_PORT}: {exc}")
            raise typer.Exit(1) from None
        _check_backend_readiness()
        typer.echo("ready")
        return
    typer.echo("ok")


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


@app.command(help=ACCESS_OPERATION.summary)
def access(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(get_access, _json_output(json_output), echo_access)


@app.command(help="Run MCP plus background auth refresh under one supervisor.")
def run(
    transport: Annotated[
        Literal["stdio", "sse", "streamable-http"],
        typer.Option(help="FastMCP transport to run."),
    ] = DEFAULT_MCP_TRANSPORT,
    host: Annotated[str, typer.Option(help="Host for HTTP MCP transports.")] = DEFAULT_HTTP_HOST,
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port for HTTP MCP transports.")] = DEFAULT_HTTP_PORT,
    mount_path: Annotated[
        str | None,
        typer.Option(help="Optional mount path for SSE transport."),
    ] = None,
    allow_external_host: Annotated[
        bool,
        typer.Option(
            "--allow-external-host",
            help="Allow HTTP MCP transports to bind outside loopback. Use only behind a trusted boundary.",
        ),
    ] = False,
) -> None:
    _validate_http_bind(host, transport, allow_external_host=allow_external_host)
    run_service(transport=transport, host=host, port=port, mount_path=mount_path)


@app.command(hidden=True)
def serve(
    transport: Annotated[
        Literal["stdio", "sse", "streamable-http"],
        typer.Option(help="FastMCP transport to run."),
    ] = DEFAULT_MCP_TRANSPORT,
    host: Annotated[str, typer.Option(help="Host for HTTP MCP transports.")] = DEFAULT_HTTP_HOST,
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port for HTTP MCP transports.")] = DEFAULT_HTTP_PORT,
    mount_path: Annotated[
        str | None,
        typer.Option(help="Optional mount path for SSE transport."),
    ] = None,
    allow_external_host: Annotated[
        bool,
        typer.Option(
            "--allow-external-host",
            help="Allow HTTP MCP transports to bind outside loopback. Use only behind a trusted boundary.",
        ),
    ] = False,
) -> None:
    _validate_http_bind(host, transport, allow_external_host=allow_external_host)
    run_mcp_server(create_mcp_server(host=host, port=port), transport=transport, mount_path=mount_path)


@app.command(help="Show repository scores, report freshness, and active work.")
def status(
    repo: Annotated[str | None, typer.Argument(help="Repo id or owner/name. Defaults to all repos.")] = None,
    sort: Annotated[
        Literal["default", "name", "weakest", "overall", "latest-report"],
        typer.Option(
            "--sort",
            help="Sort repos by default order, name, weakest score, overall score, or latest report date.",
        ),
    ] = _DEFAULT_CLI_SETTINGS.repo.default_sort,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: runtime_status(repo, _selected_project(), sort),
        _json_output(json_output),
        echo_repo_status_table,
    )


@app.command(help="Poll until all report audits for a repository have results.")
def wait(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    timeout: Annotated[
        str,
        typer.Option(
            "--timeout",
            help="Maximum wait duration, for example 30m, 2h, or 900s.",
        ),
    ] = _DEFAULT_CLI_SETTINGS.report_wait.timeout_text,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    payload = _resolve_command_payload(
        lambda: wait_for_reports(
            repo,
            _selected_project(),
            options=ReportWaitOptions(
                poll_seconds=default_settings().report_wait.poll_seconds,
                timeout_seconds=parse_duration_seconds(timeout),
                heartbeat_seconds=default_settings().report_wait.heartbeat_seconds,
            ),
            heartbeat=echo_wait_heartbeat,
        )
    )
    if _json_output(json_output):
        echo_json(payload)
    else:
        echo_wait_status(payload)
    if isinstance(payload, dict) and payload.get("complete") is False:
        raise typer.Exit(2)


@project_app.command("list", help="List Enji projects and their repository counts.")
def project_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(list_projects, _json_output(json_output), echo_project_table)


@project_app.command("create", help="Create an Enji project.")
def project_create(
    name: Annotated[str, typer.Argument(help="Project name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(lambda: create_project(name), _json_output(json_output))


@project_app.command("rename", help="Rename an Enji project.")
def project_rename(
    project: Annotated[str, typer.Argument(help="Exact project id or name.")],
    name: Annotated[str, typer.Argument(help="New project name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(lambda: rename_project(project, name), _json_output(json_output))


@project_app.command("delete", help="Delete an Enji project. Requires --yes.")
def project_delete(
    project: Annotated[str, typer.Argument(help="Exact project id or name.")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm destructive project deletion.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    if not yes:
        _echo_error("VALIDATION", "project delete requires --yes")
        raise typer.Exit(1)
    _run_human_or_json_command(lambda: delete_project(project), _json_output(json_output))


@report_app.command("read", help="Read ready report Markdown for a repository.")
def report_read(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audits: Annotated[
        list[ReportAuditAlias] | None,
        typer.Argument(help="Optional report audit aliases. Defaults to ready reports."),
    ] = None,
    all_reports: Annotated[bool, typer.Option("--all", help="Read every report audit.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    payload = _resolve_command_payload(
        lambda: read_reports_for_repo(repo, _selected_project(), _report_audits(audits or []), all_reports=all_reports)
    )
    if _json_output(json_output):
        echo_json(report_read_summary_payload(payload))
        return
    try:
        typer.echo(reports_markdown(payload))
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None


@repo_app.command("list", help="List connected repositories with triage scores.")
def repo_list(
    sort: Annotated[
        Literal["default", "name", "weakest", "overall", "latest-report"],
        typer.Option(
            "--sort",
            help="Sort repos by default order, name, weakest score, overall score, or latest report date.",
        ),
    ] = _DEFAULT_CLI_SETTINGS.repo.default_sort,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: list_project_inventory(_selected_project(), sort),
        _json_output(json_output),
        echo_repo_score_table,
    )


@repo_app.command("resolve", help="Resolve an Enji repo id or GitHub owner/name selector.")
def repo_resolve(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: resolve_repo(repo, _selected_project()),
        _json_output(json_output),
        echo_repo_resolve_table,
    )


@repo_app.command("connect", help="Connect a GitHub owner/name repository to Enji Guard.")
def repo_connect(
    github_repo: Annotated[str, typer.Argument(help="GitHub owner/name repository slug.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(lambda: connect_repo(github_repo, _selected_project()), _json_output(json_output))


@repo_app.command("move", help="Move a repository to another Enji project.")
def repo_move(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    to_project: Annotated[str, typer.Option("--to-project", help="Destination exact Enji project id or name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(lambda: move_repo(repo, _selected_project(), to_project), _json_output(json_output))


@recon_app.command("start", help="Start baseline discovery for a connected repository.")
def recon_start(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(lambda: start_recon(repo, _selected_project()), _json_output(json_output))


@audit_app.command("start", help="Start one or more slow report-producing audits.")
def audit_start(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audits: Annotated[
        list[ReportAuditAlias] | None,
        typer.Argument(help="One or more canonical report audit aliases. Use --all for all report audits."),
    ] = None,
    all_reports: Annotated[bool, typer.Option("--all", help="Start every report audit.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: start_report_audits(repo, _selected_project(), _report_audits(audits or []), all_reports=all_reports),
        _json_output(json_output),
    )


@schedule_app.command("list", help="List automatic report audit schedules.")
def schedule_list(
    repo: Annotated[
        str | None,
        typer.Argument(help="Optional repo id or owner/name. Defaults to every repo in scope."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: list_schedule_settings(repo, _selected_project()),
        _json_output(json_output),
        echo_schedule_settings_table,
    )


@schedule_app.command(
    "set",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Batch update automatic report audit schedules.",
    epilog=SCHEDULE_SET_EPILOG,
    options_metavar="[OPTIONS] [REPO]",
)
def schedule_set(ctx: typer.Context) -> None:
    try:
        args = parse_schedule_set_args(ctx.args)
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    _run_human_or_json_command(
        lambda: set_schedule_settings(
            args.repo,
            _selected_project(),
            ScheduleSettingsUpdate(
                enabled=_preference_switch(args.enabled),
                frequency=args.frequency,
                days_of_week=None,
                schedule_time=None,
                timezone=args.timezone,
            ),
            all_repos=args.all_repos,
            all_projects=args.all_projects,
        ),
        _json_output(args.json_output),
        echo_schedule_settings_table,
    )


@schedule_app.command("auto-time", help="Let Enji choose automatic report audit times.")
def schedule_auto_time(
    repo: Annotated[
        str | None,
        typer.Argument(help="Optional repo id or owner/name for a single-repo update."),
    ] = None,
    all_repos: Annotated[bool, typer.Option("--all-repos", help="Batch every repo in the selected --project.")] = False,
    all_projects: Annotated[bool, typer.Option("--all-projects", help="Batch every repo in every project.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: set_schedule_settings(
            repo,
            _selected_project(),
            ScheduleSettingsUpdate(
                enabled=None,
                frequency=None,
                days_of_week=None,
                schedule_time="auto",
                timezone=None,
            ),
            all_repos=all_repos,
            all_projects=all_projects,
        ),
        _json_output(json_output),
        echo_schedule_settings_table,
    )


@email_app.command("list", help="List manual and scheduled report email preferences.")
def email_list(
    repo: Annotated[
        str | None,
        typer.Argument(help="Optional repo id or owner/name. Defaults to every repo in scope."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: list_email_preferences(repo, _selected_project()),
        _json_output(json_output),
        echo_email_preferences_table,
    )


@email_app.command(
    "set",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Batch update report email preferences.",
    epilog=EMAIL_SET_EPILOG,
    options_metavar="[OPTIONS] [REPO]",
)
def email_set(ctx: typer.Context) -> None:
    try:
        args = parse_email_set_args(ctx.args)
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    _run_human_or_json_command(
        lambda: set_email_preferences(
            args.repo,
            _selected_project(),
            EmailPreferenceUpdate(
                manual_run_completion=_preference_switch(args.manual),
                scheduled_run_completion=_preference_switch(args.scheduled),
            ),
            all_repos=args.all_repos,
            all_projects=args.all_projects,
        ),
        _json_output(args.json_output),
        echo_email_preferences_table,
    )


@catalog_app.command("audits", help=CATALOG_AUDITS_OPERATION.summary)
def catalog_audits(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    payload = resolve_operation_result(CATALOG_AUDITS_OPERATION.execute())
    if _json_output(json_output):
        echo_json(payload)
        return
    echo_audit_catalog(payload)


@catalog_app.command("audit", help=CATALOG_AUDIT_OPERATION.summary)
def catalog_audit(
    audit: Annotated[AuditAlias, typer.Argument(help="Canonical audit alias.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    payload = resolve_operation_result(CATALOG_AUDIT_OPERATION.execute(audit))
    if _json_output(json_output):
        echo_json(payload)
        return
    echo_key_values(object_dict(payload))


@auth_app.command("import-cookie", help="Import a raw browser Cookie header from stdin.")
def auth_import_cookie(
    stdin: Annotated[bool, typer.Option("--stdin", help="Read a raw Cookie header from stdin.")] = False,
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ~/.config/enji-guard/auth.json."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    if not stdin:
        _echo_error("VALIDATION", "use --stdin to avoid storing cookies in shell history")
        raise typer.Exit(1)

    raw_cookie = sys.stdin.read()
    try:
        payload = import_cookie(raw_cookie, auth_file)
        if _json_output(json_output):
            echo_json(payload)
        else:
            echo_key_values(cast(dict[str, object], payload))
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    except OSError as exc:
        _echo_error("STORAGE", str(exc))
        raise typer.Exit(1) from None


@auth_app.command("import-token", help="Import a bearer or API token from stdin.")
def auth_import_token(
    stdin: Annotated[bool, typer.Option("--stdin", help="Read a bearer token from stdin.")] = False,
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ~/.config/enji-guard/auth.json."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    if not stdin:
        _echo_error("VALIDATION", "use --stdin to avoid storing tokens in shell history")
        raise typer.Exit(1)

    raw_token = sys.stdin.read()
    try:
        payload = import_bearer_token(raw_token, auth_file)
        if _json_output(json_output):
            echo_json(payload)
        else:
            echo_key_values(cast(dict[str, object], payload))
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
        typer.Option("--auth-file", help="Auth file path. Defaults to ~/.config/enji-guard/auth.json."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    payload = cast_auth_status_payload(resolve_operation_result(auth_status(auth_file)))
    if _json_output(json_output):
        echo_json(payload)
    else:
        echo_auth_status(payload)
    if not payload["authenticated"]:
        raise typer.Exit(3)


@auth_app.command("refresh", help="Refresh cookie auth and persist rotated cookies.")
def auth_refresh_command(
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ~/.config/enji-guard/auth.json."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    try:
        payload = refresh_auth(auth_file)
        if _json_output(json_output):
            echo_json(payload)
        else:
            echo_key_values(cast(dict[str, object], payload))
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


def _preference_switch(value: Literal["on", "off"] | None) -> bool | None:
    if value == "on":
        return True
    if value == "off":
        return False
    return None


def _selected_project() -> str | None:
    project = _cli_state["project"]
    return project if isinstance(project, str) else None


def _json_output(local_json_output: bool = False) -> bool:
    return local_json_output or _cli_state["json"] is True


def _report_audit(audit: ReportAuditAlias) -> AuditAlias:
    return AuditAlias(audit.value)


def _report_audits(audits: list[ReportAuditAlias]) -> list[AuditAlias]:
    return [_report_audit(audit) for audit in audits]
