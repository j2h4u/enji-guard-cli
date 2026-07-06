from collections.abc import Callable
from typing import Annotated, Literal, cast

import typer

from enji_guard_cli.audits import AuditAlias, ReportAuditAlias
from enji_guard_cli.cli_impl.auth_catalog import (
    SharedCliConfig,
    auth_app,
    catalog_app,
    configure_auth_catalog_commands,
    set_auth_refresh_action,
    set_auth_status_action,
)
from enji_guard_cli.cli_impl.durations import parse_duration_seconds
from enji_guard_cli.cli_impl.rendering import (
    echo_access,
    echo_audit_start,
    echo_email_preferences_table,
    echo_generic_payload,
    echo_json,
    echo_key_values,
    echo_project_table,
    echo_repo_add,
    echo_repo_resolve_table,
    echo_repo_score_table,
    echo_repo_status_table,
    echo_schedule_settings_table,
    echo_wait_heartbeat,
    echo_wait_status,
)
from enji_guard_cli.cli_impl.report_rendering import echo_report_summary, report_summary_payload, reports_markdown
from enji_guard_cli.cli_impl.runtime_controls import (
    _check_backend_readiness,
    _check_local_listener,
    _run_service_body,
    _serve_body,
)
from enji_guard_cli.cli_impl.write_targets import (
    EmailSetCliArgs,
    ScheduleSetCliArgs,
    parse_email_set_args,
    parse_schedule_set_args,
)
from enji_guard_cli.core import (
    EmailPreferenceUpdate,
    OperationName,
    OperationResult,
    ReportWaitOptions,
    ScheduleSettingsUpdate,
    add_repo,
    create_project,
    delete_project,
    list_email_preferences,
    list_project_inventory,
    list_projects,
    list_schedule_settings,
    move_repo,
    package_version,
    read_reports_for_repo,
    refresh_auth,
    remove_repo,
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
from enji_guard_cli.journey import AgentJourney, run_agent_journey
from enji_guard_cli.settings import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT, DEFAULT_MCP_TRANSPORT, default_settings
from enji_guard_cli.telemetry import configure_logging

MAIN_HELP = """Agent-oriented CLI for Enji Guard repository audits.

Model: projects group GitHub repositories. Pass the known owner/name repo
selector directly when an agent is working on a specific checkout. Recon is
baseline discovery; report audits are separate slow jobs that produce scores
and readable reports. Use status/list for triage, audit start for work,
wait/status for long-running jobs, report summary for compact metadata, and
report read for the Markdown findings. Text is the default; add --json for
automation.
"""

app = typer.Typer(help=MAIN_HELP)
project_app = typer.Typer(help="List and manage Enji projects.")
repo_app = typer.Typer(help="Discover, resolve, add, remove, and move GitHub repositories.")
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
_CLI_COMMAND_ROOT = "enji-guard"

type JsonCommandAction = Callable[[], OperationResult]
type CommandBody = Callable[[], object]


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
    *,
    journey: AgentJourney,
) -> None:
    def _body() -> object:
        payload = _resolve_command_payload(action)
        if json_output:
            echo_json(payload)
        else:
            renderer = human_renderer if human_renderer is not None else echo_generic_payload
            renderer(payload)
        return payload

    _run_cli_journey(
        _body,
        command_path=journey.operation,
        json_output=json_output,
        selector_kind=journey.selector_kind,
        all_flag=journey.all_flag,
    )


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


def _run_cli_journey(
    body: CommandBody,
    *,
    command_path: str | None = None,
    json_output: bool | None = None,
    selector_kind: str = "unknown",
    all_flag: bool | None = None,
) -> object:
    resolved_journey = _cli_journey(
        command_path if command_path is not None else _CLI_COMMAND_ROOT,
        json_output=_json_output() if json_output is None else json_output,
        selector_kind=selector_kind,
        all_flag=all_flag,
    )
    return run_agent_journey(body, resolved_journey, exit_code_for_exception=_cli_exit_code_for_exception)


def _cli_journey(
    command_path: str,
    *,
    json_output: bool = False,
    selector_kind: str = "unknown",
    all_flag: bool | None = None,
) -> AgentJourney:
    return AgentJourney(
        event_prefix="cli_command",
        operation=command_path,
        surface="cli",
        json_output=json_output,
        selector_kind=selector_kind,
        all_flag=all_flag,
    )


def _cli_exit_code_for_exception(exc: Exception) -> int:
    if isinstance(exc, typer.Exit):
        return int(exc.exit_code) if exc.exit_code is not None else 0
    return 1


def _command_path(*parts: str) -> str:
    return " ".join((_CLI_COMMAND_ROOT, *parts))


def _selector_kind_for_repo(repo: str | None, *, project: str | None = None, all_flag: bool = False) -> str:
    if all_flag:
        return "all"
    if repo is not None:
        return "owner_name" if "/" in repo else "repo_id"
    if project is not None:
        return "project"
    return "unknown"


def _selector_kind_for_github_repo(github_repo: str) -> str:
    return "owner_name" if "/" in github_repo else "unknown"


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show the installed version and exit.")] = False,
    project: Annotated[
        str | None,
        typer.Option("--project", help="Global exact Enji project id or name filter."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _cli_state["project"] = project
    _cli_state["json"] = json_output
    configure_logging(provenance=_cli_provenance(ctx.invoked_subcommand))
    if version:
        _run_cli_journey(
            _version_body,
            command_path=_command_path("--version"),
            json_output=json_output,
            selector_kind="unknown",
        )


def _cli_provenance(command: str | None) -> str:
    if command == "run":
        return "supervisor"
    if command == "serve":
        return "mcp"
    return "cli"


def _version_body() -> object:
    typer.echo(package_version())
    raise typer.Exit


@app.command(help="Return process liveness or full service readiness.")
def health(
    ready: Annotated[
        bool,
        typer.Option("--ready", help="Also check local MCP and cached Enji backend readiness."),
    ] = False,
) -> None:
    _run_cli_journey(
        lambda: _health_body(ready),
        command_path=_command_path("health"),
        json_output=_json_output(),
        selector_kind="unknown",
    )


def _health_body(ready: bool) -> object:
    if ready:
        try:
            _check_local_listener(DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT)
        except OSError as exc:
            _echo_error("UNREADY", f"MCP listener is not ready at {DEFAULT_HTTP_HOST}:{DEFAULT_HTTP_PORT}: {exc}")
            raise typer.Exit(1) from None
        _check_backend_readiness()
        typer.echo("ready")
        return None
    typer.echo("ok")
    return None


@app.command(help=ACCESS_OPERATION.summary)
def access(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        get_access,
        _json_output(json_output),
        echo_access,
        journey=_cli_journey(command_path=_command_path("access")),
    )


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
    _run_cli_journey(
        lambda: _run_service_body(transport, host, port, mount_path, allow_external_host),
        command_path=_command_path("run"),
        json_output=_json_output(),
        selector_kind="unknown",
    )


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
    _run_cli_journey(
        lambda: _serve_body(transport, host, port, mount_path, allow_external_host),
        command_path=_command_path("serve"),
        json_output=_json_output(),
        selector_kind="unknown",
    )


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
        journey=_cli_journey(
            command_path=_command_path("status"),
            selector_kind=_selector_kind_for_repo(repo, project=_selected_project()),
        ),
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
    _run_cli_journey(
        lambda: _wait_body(repo=repo, timeout=timeout, json_output=json_output),
        command_path=_command_path("wait"),
        json_output=_json_output(json_output),
        selector_kind=_selector_kind_for_repo(repo, project=_selected_project()),
    )


def _wait_body(*, repo: str, timeout: str, json_output: bool) -> object:
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
    return payload


@project_app.command("list", help="List Enji projects and their repository counts.")
def project_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        list_projects,
        _json_output(json_output),
        echo_project_table,
        journey=_cli_journey(
            command_path=_command_path("project", "list"),
            selector_kind=_selector_kind_for_repo(None, project=_selected_project()),
        ),
    )


@project_app.command("create", help="Create an Enji project.")
def project_create(
    name: Annotated[str, typer.Argument(help="Project name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: create_project(name),
        _json_output(json_output),
        journey=_cli_journey(command_path=_command_path("project", "create"), selector_kind="project"),
    )


@project_app.command("rename", help="Rename an Enji project.")
def project_rename(
    project: Annotated[str, typer.Argument(help="Exact project id or name.")],
    name: Annotated[str, typer.Argument(help="New project name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: rename_project(project, name),
        _json_output(json_output),
        journey=_cli_journey(command_path=_command_path("project", "rename"), selector_kind="project"),
    )


@project_app.command("delete", help="Delete an empty Enji project.")
def project_delete(
    project: Annotated[str, typer.Argument(help="Exact project id or name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_cli_journey(
        lambda: _project_delete_body(project=project, json_output=json_output),
        command_path=_command_path("project", "delete"),
        json_output=_json_output(json_output),
        selector_kind="project",
    )


def _project_delete_body(*, project: str, json_output: bool) -> object:
    payload = _resolve_command_payload(lambda: delete_project(project))
    if _json_output(json_output):
        echo_json(payload)
    else:
        echo_key_values(cast(dict[str, object], payload))
    return payload


@report_app.command("read", help="Read report bodies for a repository. Default output is Markdown.")
def report_read(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audits: Annotated[
        list[ReportAuditAlias] | None,
        typer.Argument(help="Optional report audit aliases. Defaults to ready reports."),
    ] = None,
    all_reports: Annotated[bool, typer.Option("--all", help="Read every report audit.")] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the full structured read payload, including report Markdown bodies."),
    ] = False,
) -> None:
    _run_cli_journey(
        lambda: _report_read_body(repo=repo, audits=audits, all_reports=all_reports, json_output=json_output),
        command_path=_command_path("report", "read"),
        json_output=_json_output(json_output),
        selector_kind=_selector_kind_for_repo(repo, project=_selected_project(), all_flag=all_reports),
        all_flag=all_reports,
    )


def _report_read_body(
    *,
    repo: str,
    audits: list[ReportAuditAlias] | None,
    all_reports: bool,
    json_output: bool,
) -> object:
    payload = _resolve_command_payload(
        lambda: read_reports_for_repo(repo, _selected_project(), _report_audits(audits or []), all_reports=all_reports)
    )
    if _json_output(json_output):
        echo_json(payload)
        return payload
    try:
        typer.echo(reports_markdown(payload))
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    return payload


@report_app.command("summary", help="Read compact report metadata for a repository.")
def report_summary(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audits: Annotated[
        list[ReportAuditAlias] | None,
        typer.Argument(help="Optional report audit aliases. Defaults to ready reports."),
    ] = None,
    all_reports: Annotated[bool, typer.Option("--all", help="Summarize every report audit.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit compact structured report summary output.")] = False,
) -> None:
    _run_cli_journey(
        lambda: _report_summary_body(repo=repo, audits=audits, all_reports=all_reports, json_output=json_output),
        command_path=_command_path("report", "summary"),
        json_output=_json_output(json_output),
        selector_kind=_selector_kind_for_repo(repo, project=_selected_project(), all_flag=all_reports),
        all_flag=all_reports,
    )


def _report_summary_body(
    *,
    repo: str,
    audits: list[ReportAuditAlias] | None,
    all_reports: bool,
    json_output: bool,
) -> object:
    payload = _resolve_command_payload(
        lambda: read_reports_for_repo(repo, _selected_project(), _report_audits(audits or []), all_reports=all_reports)
    )
    if _json_output(json_output):
        echo_json(report_summary_payload(payload))
    else:
        echo_report_summary(payload)
    return payload


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
        journey=_cli_journey(
            command_path=_command_path("repo", "list"),
            selector_kind=_selector_kind_for_repo(None, project=_selected_project()),
        ),
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
        journey=_cli_journey(
            command_path=_command_path("repo", "resolve"),
            selector_kind=_selector_kind_for_repo(repo, project=_selected_project()),
        ),
    )


@repo_app.command("add", help="Add a GitHub owner/name repository to an Enji project.")
def repo_add(
    github_repo: Annotated[str, typer.Argument(help="GitHub owner/name repository slug.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: add_repo(github_repo, _selected_project()),
        _json_output(json_output),
        echo_repo_add,
        journey=_cli_journey(
            command_path=_command_path("repo", "add"),
            selector_kind=_selector_kind_for_github_repo(github_repo),
        ),
    )


@repo_app.command("remove", help="Remove a repository from an Enji project.")
def repo_remove(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: remove_repo(repo, _selected_project()),
        _json_output(json_output),
        journey=_cli_journey(
            command_path=_command_path("repo", "remove"),
            selector_kind=_selector_kind_for_repo(repo, project=_selected_project()),
        ),
    )


@repo_app.command("move", help="Move a repository to another Enji project.")
def repo_move(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    to_project: Annotated[str, typer.Option("--to-project", help="Destination exact Enji project id or name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: move_repo(repo, _selected_project(), to_project),
        _json_output(json_output),
        journey=_cli_journey(
            command_path=_command_path("repo", "move"),
            selector_kind=_selector_kind_for_repo(repo, project=_selected_project()),
        ),
    )


@recon_app.command("start", help="Start baseline discovery for a connected repository.")
def recon_start(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: start_recon(repo, _selected_project()),
        _json_output(json_output),
        journey=_cli_journey(
            command_path=_command_path("recon", "start"),
            selector_kind=_selector_kind_for_repo(repo, project=_selected_project()),
        ),
    )


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
    _run_cli_journey(
        lambda: _audit_start_body(repo=repo, audits=audits, all_reports=all_reports, json_output=json_output),
        command_path=_command_path("audit", "start"),
        json_output=_json_output(json_output),
        selector_kind=_selector_kind_for_repo(repo, project=_selected_project(), all_flag=all_reports),
        all_flag=all_reports,
    )


def _audit_start_body(
    *,
    repo: str,
    audits: list[ReportAuditAlias] | None,
    all_reports: bool,
    json_output: bool,
) -> object:
    payload = _resolve_command_payload(
        lambda: start_report_audits(repo, _selected_project(), _report_audits(audits or []), all_reports=all_reports)
    )
    if _json_output(json_output):
        echo_json(payload)
    else:
        echo_audit_start(payload)
    return payload


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
        journey=_cli_journey(
            command_path=_command_path("schedule", "list"),
            selector_kind=_selector_kind_for_repo(repo, project=_selected_project()),
        ),
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
    _run_cli_journey(
        lambda: _schedule_set_body(args),
        command_path=_command_path("schedule", "set"),
        json_output=args.json_output,
        selector_kind=_selector_kind_for_repo(
            args.repo, project=_selected_project(), all_flag=args.all_repos or args.all_projects
        ),
        all_flag=args.all_repos or args.all_projects,
    )


def _schedule_set_body(args: ScheduleSetCliArgs) -> object:
    payload = _resolve_command_payload(
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
    )
    if _json_output(args.json_output):
        echo_json(payload)
    else:
        echo_schedule_settings_table(payload)
    return payload


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
        journey=_cli_journey(
            command_path=_command_path("schedule", "auto-time"),
            selector_kind=_selector_kind_for_repo(
                repo, project=_selected_project(), all_flag=all_repos or all_projects
            ),
            all_flag=all_repos or all_projects,
        ),
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
        journey=_cli_journey(
            command_path=_command_path("email", "list"),
            selector_kind=_selector_kind_for_repo(repo, project=_selected_project()),
        ),
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
    _run_cli_journey(
        lambda: _email_set_body(args),
        command_path=_command_path("email", "set"),
        json_output=args.json_output,
        selector_kind=_selector_kind_for_repo(
            args.repo, project=_selected_project(), all_flag=args.all_repos or args.all_projects
        ),
        all_flag=args.all_repos or args.all_projects,
    )


def _email_set_body(args: EmailSetCliArgs) -> object:
    payload = _resolve_command_payload(
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
    )
    if _json_output(args.json_output):
        echo_json(payload)
    else:
        echo_email_preferences_table(payload)
    return payload


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


configure_auth_catalog_commands(
    SharedCliConfig(
        run_cli_journey=_run_cli_journey,
        command_path=_command_path,
        json_output=_json_output,
        echo_error=_echo_error,
        exit_code_for_error=_exit_code_for_error,
    )
)
set_auth_status_action(lambda auth_file: auth_status(auth_file))
set_auth_refresh_action(lambda auth_file: refresh_auth(auth_file))
