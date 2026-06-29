import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, TypeGuard, cast

import typer

from enji_guard_cli.audits import AuditAlias, ReportAuditAlias
from enji_guard_cli.auth import AuthError, AuthStatusPayload, import_bearer_token, import_cookie, refresh_auth
from enji_guard_cli.core import (
    DEFAULT_REPO_SORT,
    REPORTS_LIST_DEFAULT_SELECTOR,
    EmailPreferenceUpdate,
    OperationName,
    OperationResult,
    ScheduleSettingsUpdate,
    connect_repo,
    create_project,
    delete_project,
    list_email_preferences,
    list_project_inventory,
    list_projects,
    list_schedule_settings,
    move_repo,
    package_version,
    read_reports_for_repo,
    rename_project,
    resolve_operation_result,
    resolve_operation_spec,
    resolve_repo,
    runtime_status,
    set_email_preferences,
    set_schedule_settings,
    show_report_for_repo,
    start_recon,
    start_report_audits,
    wait_for_work,
)
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.mcp_server import create_mcp_server, run_mcp_server
from enji_guard_cli.runtime import run_service
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
report_app = typer.Typer(help="List and read generated audit reports.")
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
REPORTS_LIST_OPERATION = resolve_operation_spec(OperationName.REPORTS_LIST)
AUTH_STATUS_OPERATION = resolve_operation_spec(OperationName.AUTH_STATUS)

get_access = ACCESS_OPERATION.execute
get_reports_list = REPORTS_LIST_OPERATION.execute
auth_status = AUTH_STATUS_OPERATION.execute
_cli_state: dict[str, object] = {"project": None, "json": False}

type JsonCommandAction = Callable[[], OperationResult]


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, sort_keys=True))


def _echo_error(code: str, message: str) -> None:
    typer.echo(f"{code}: {message}", err=True)


def _run_human_or_json_command(
    action: JsonCommandAction,
    json_output: bool,
    human_renderer: Callable[[object], None] | None = None,
) -> None:
    payload = _resolve_command_payload(action)
    if json_output:
        _echo_json(payload)
        return
    renderer = human_renderer if human_renderer is not None else _echo_generic_payload
    renderer(payload)


def _echo_table(headers: tuple[str, ...], rows: list[tuple[str, ...]], empty_message: str = "No rows.") -> None:
    if not rows:
        typer.echo(empty_message)
        return
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row, strict=True)]
    typer.echo(_table_line(headers, widths))
    typer.echo(_table_line(tuple("-" * width for width in widths), widths))
    for row in rows:
        typer.echo(_table_line(row, widths))


def _table_line(cells: tuple[str, ...], widths: list[int]) -> str:
    return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))


def _echo_repo_score_table(payload: object) -> None:
    headers = (
        "project",
        "repo",
        "state",
        "last_report",
        "overall",
        "grade",
        "weakest",
        "vulns",
        "ai",
        "tests",
        "tech",
        "deps",
        "cog",
        "dead",
    )
    _echo_table(
        headers,
        [_repo_score_row(project, repo) for project, repo in _payload_repos(payload)],
        "No repositories.",
    )


def _echo_repo_status_table(payload: object) -> None:
    headers = (
        "project",
        "repo",
        "state",
        "overall",
        "weakest",
        "reports",
        "active",
        "last_report",
        "current",
        "audited",
    )
    _echo_table(
        headers,
        [_repo_status_row(project, repo) for project, repo in _payload_repos(payload)],
        "No repositories.",
    )


def _echo_project_table(payload: object) -> None:
    headers = ("project", "id", "repos", "recon", "score_axes")
    _echo_table(headers, [_project_row(project) for project in _payload_projects(payload)], "No projects.")


def _echo_repo_resolve_table(payload: object) -> None:
    data = _object_dict(payload)
    headers = ("selector", "resolved", "project", "repo", "repo_id", "state")
    rows = [_repo_resolve_row(data, _object_dict(match)) for match in _object_list(data.get("matches"))]
    _echo_table(headers, rows, "No repositories.")


def _echo_report_inventory_table(payload: object) -> None:
    headers = ("project", "id", "repos", "recon", "score_axes")
    _echo_table(headers, [_project_row(project) for project in _payload_projects(payload)], "No projects.")


def _echo_email_preferences_table(payload: object) -> None:
    headers = ("project", "repo", "audit", "manual", "auto")
    rows = [
        (
            _text_cell(row.get("project_name"), fallback=_text_cell(row.get("project_id"))),
            _text_cell(row.get("github_repo"), fallback=_text_cell(row.get("repo_id"))),
            _text_cell(row.get("audit")),
            _text_cell(row.get("manual_run_completion")),
            _text_cell(row.get("scheduled_run_completion")),
        )
        for row in (_object_dict(item) for item in _object_list(_object_dict(payload).get("preferences")))
    ]
    _echo_table(headers, rows, "No email preferences.")


def _echo_schedule_settings_table(payload: object) -> None:
    schedule_rows = [_object_dict(item) for item in _object_list(_object_dict(payload).get("schedules"))]
    headers = _schedule_settings_headers(schedule_rows)
    rows = [_schedule_settings_row(row, include_status="status" in headers) for row in schedule_rows]
    _echo_table(headers, rows, "No schedules.")


def _schedule_settings_headers(rows: list[dict[str, object]]) -> tuple[str, ...]:
    base = ("project", "repo", "audit", "enabled", "freq", "days", "at", "timezone")
    if any("status" in row for row in rows):
        return (*base, "status")
    return base


def _schedule_settings_row(row: dict[str, object], *, include_status: bool) -> tuple[str, ...]:
    base = (
        _text_cell(row.get("project_name"), fallback=_text_cell(row.get("project_id"))),
        _text_cell(row.get("github_repo"), fallback=_text_cell(row.get("repo_id"))),
        _text_cell(row.get("audit")),
        _text_cell(row.get("enabled")),
        _text_cell(row.get("frequency")),
        _days_cell(row.get("days_of_week")),
        _schedule_at_cell(row),
        _text_cell(row.get("timezone")),
    )
    if include_status:
        return (*base, _text_cell(row.get("status")))
    return base


def _echo_auth_status(payload: object) -> None:
    data = _object_dict(payload)
    authenticated = "yes" if data.get("authenticated") is True else "no"
    typer.echo(f"authenticated: {authenticated}")
    for key in ("credential_type", "email", "name", "user_id", "auth_file", "code", "message"):
        value = _text_cell(data.get(key))
        if value != "-":
            typer.echo(f"{key}: {value}")


def _echo_audit_catalog(payload: object) -> None:
    headers = ("audit", "label", "job_kind", "route")
    rows = [
        (
            _text_cell(audit.get("alias")),
            _text_cell(audit.get("label")),
            _text_cell(audit.get("job_kind")),
            _text_cell(audit.get("route_slug")),
        )
        for audit in (_object_dict(item) for item in _object_list(payload))
    ]
    _echo_table(headers, rows, "No audits.")


def _echo_wait_status(payload: object) -> None:
    data = _object_dict(payload)
    idle = "yes" if data.get("idle") is True else "no"
    typer.echo(f"idle: {idle}")
    for key in ("repo_id", "audit", "elapsed_seconds"):
        typer.echo(f"{key}: {_text_cell(data.get(key))}")
    typer.echo(f"active_runs: {len(_object_list(data.get('active_runs')))}")


def _echo_generic_payload(payload: object) -> None:
    _echo_key_values(_object_dict(payload))


def _echo_key_values(payload: dict[str, object]) -> None:
    if not payload:
        typer.echo("No data.")
        return
    for key, value in payload.items():
        typer.echo(f"{key}: {_value_cell(value)}")


def _payload_projects(payload: object) -> list[dict[str, object]]:
    return [_object_dict(project) for project in _object_list(_object_dict(payload).get("projects"))]


def _payload_repos(payload: object) -> list[tuple[dict[str, object], dict[str, object]]]:
    repos: list[tuple[dict[str, object], dict[str, object]]] = []
    for project in _payload_projects(payload):
        repos.extend((project, _object_dict(repo_value)) for repo_value in _object_list(project.get("repos")))
    return repos


def _project_row(project: dict[str, object]) -> tuple[str, ...]:
    return (
        _project_label(project),
        _text_cell(project.get("id"), fallback=_text_cell(project.get("project_id"))),
        str(_repo_count(project)),
        _project_recon_cell(project),
        str(len(_object_dict(project.get("scores")))),
    )


def _repo_resolve_row(data: dict[str, object], match: dict[str, object]) -> tuple[str, ...]:
    return (
        _text_cell(data.get("selector")),
        _text_cell(data.get("resolved")),
        _project_label(match),
        _repo_label(match),
        _text_cell(match.get("repo_id")),
        _repo_state(match),
    )


def _repo_score_row(project: dict[str, object], repo: dict[str, object]) -> tuple[str, ...]:
    scores = _object_dict(repo.get("scores"))
    score_summary = _object_dict(repo.get("score_summary"))
    return (
        _project_label(project),
        _repo_label(repo),
        _repo_state(repo),
        _date_cell(repo.get("last_report_at")),
        _score_cell(score_summary.get("overall_score")),
        _text_cell(score_summary.get("overall_grade")),
        _weakest_cell(score_summary),
        _score_cell(scores.get("vulns")),
        _score_cell(scores.get("ai-readiness")),
        _score_cell(scores.get("tests")),
        _score_cell(scores.get("tech-health")),
        _score_cell(scores.get("dependency-hygiene")),
        _score_cell(scores.get("cognitive-debt")),
        _score_cell(scores.get("dead-code")),
    )


def _repo_status_row(project: dict[str, object], repo: dict[str, object]) -> tuple[str, ...]:
    score_summary = _object_dict(repo.get("score_summary"))
    reports = _object_dict(repo.get("reports"))
    return (
        _project_label(project),
        _repo_label(repo),
        _repo_state(repo),
        _score_cell(score_summary.get("overall_score")),
        _weakest_cell(score_summary),
        _reports_cell(reports),
        _text_cell(repo.get("active_run_count")),
        _date_cell(repo.get("last_report_at")),
        _sha_cell(repo.get("current_head_sha")),
        _sha_cell(_audited_head(reports)),
    )


def _project_label(project: dict[str, object]) -> str:
    return _text_cell(
        project.get("project_name"),
        fallback=_text_cell(project.get("name"), fallback=_text_cell(project.get("project_id"))),
    )


def _repo_label(repo: dict[str, object]) -> str:
    return _text_cell(repo.get("github_repo"), fallback=_text_cell(repo.get("repo_id")))


def _repo_state(repo: dict[str, object]) -> str:
    if repo.get("connected") is not True:
        return "disconnected"
    if repo.get("recon_done") is not True:
        return "uninitialized"
    if not _object_dict(repo.get("scores")):
        return "unscored"
    return "scored"


def _repo_count(project: dict[str, object]) -> int:
    repo_ids = _object_list(project.get("repo_ids"))
    if repo_ids:
        return len(repo_ids)
    camel_repo_ids = _object_list(project.get("repoIds"))
    if camel_repo_ids:
        return len(camel_repo_ids)
    return len(_object_list(project.get("repos")))


def _project_recon_cell(project: dict[str, object]) -> str:
    value = project.get("recon_pending")
    if value is None:
        value = project.get("reconPending")
    if value is True:
        return "pending"
    if value is False:
        return "done"
    return "-"


def _weakest_cell(score_summary: dict[str, object]) -> str:
    axis = score_summary.get("weakest_axis")
    score = _score_cell(score_summary.get("weakest_score"))
    if not isinstance(axis, str) or score == "-":
        return "-"
    return f"{axis}={score}"


def _reports_cell(reports: dict[str, object]) -> str:
    ready = len(_object_list(reports.get("ready")))
    running = len(_object_list(reports.get("running")))
    missing = len(_object_list(reports.get("missing")))
    parts: list[str] = []
    if ready:
        parts.append(f"{ready} ready")
    if running:
        parts.append(f"{running} running")
    if missing:
        parts.append(f"{missing} missing")
    return ", ".join(parts) if parts else "-"


def _audited_head(reports: dict[str, object]) -> object | None:
    for report_value in _object_list(reports.get("reports")):
        report = _object_dict(report_value)
        audited_head = report.get("last_audited_head_sha")
        if isinstance(audited_head, str) and audited_head:
            return audited_head
    return None


def _sha_cell(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "-"
    return value[:8]


def _date_cell(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "-"
    return value[:10]


def _days_cell(value: object) -> str:
    days = [item for item in _object_list(value) if isinstance(item, str)]
    if not days:
        return "-"
    return ",".join(days)


def _schedule_at_cell(row: dict[str, object]) -> str:
    if row.get("schedule_time_source") == "auto":
        return "auto"
    return _text_cell(row.get("schedule_time"))


def _score_cell(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.1f}".removesuffix(".0")
    return "-"


def _text_cell(value: object, *, fallback: str = "-") -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    return fallback


def _value_cell(value: object) -> str:
    if isinstance(value, str | int | float) or value is None or isinstance(value, bool):
        return _text_cell(value)
    if isinstance(value, list):
        return f"{len(value)} item(s)"
    if isinstance(value, dict):
        return f"{len(value)} field(s)"
    return str(value)


def _object_dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _object_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


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
    _cli_state["json"] = json_output
    configure_logging(log_level, log_format)
    if version:
        typer.echo(package_version())
        raise typer.Exit


@app.command(help="Return local process liveness.")
def health() -> None:
    typer.echo("ok")


@app.command(help=ACCESS_OPERATION.summary)
def access(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(get_access, _json_output(json_output))


@app.command(help="Run MCP plus background auth refresh under one supervisor.")
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


@app.command(help="Show repository scores, report freshness, and active work.")
def status(
    repo: Annotated[str | None, typer.Argument(help="Repo id or owner/name. Defaults to all repos.")] = None,
    sort: Annotated[
        Literal["default", "name", "weakest", "overall", "latest-report"],
        typer.Option(
            "--sort",
            help="Sort repos by default order, name, weakest score, overall score, or latest report date.",
        ),
    ] = DEFAULT_REPO_SORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: runtime_status(repo, _selected_project(), sort),
        _json_output(json_output),
        _echo_repo_status_table,
    )


@app.command(help="Poll until one recon or report audit is no longer running.")
def wait(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audit: Annotated[AuditAlias, typer.Argument(help="recon or canonical report audit alias.")],
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, help="Maximum wait time in seconds."),
    ] = 7200,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    payload = _resolve_command_payload(
        lambda: wait_for_work(repo, audit, _selected_project(), poll_seconds=10, timeout_seconds=timeout_seconds)
    )
    if _json_output(json_output):
        _echo_json(payload)
    else:
        _echo_wait_status(payload)
    if isinstance(payload, dict) and payload.get("idle") is False:
        raise typer.Exit(2)


@project_app.command("list", help="List Enji projects and their repository counts.")
def project_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(list_projects, _json_output(json_output), _echo_project_table)


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


@report_app.command("list", help=REPORTS_LIST_OPERATION.summary)
def report_list(
    selector: Annotated[
        str,
        typer.Option("--selector", help="Repository selector. Defaults to '*'."),
    ] = REPORTS_LIST_DEFAULT_SELECTOR,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: get_reports_list(selector=selector),
        _json_output(json_output),
        _echo_report_inventory_table,
    )


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
        _echo_json(payload)
        return
    try:
        typer.echo(_reports_markdown(payload))
    except ValueError as exc:
        _echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None


@report_app.command("show", help="Read one report audit as Markdown.")
def report_show(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audit: Annotated[ReportAuditAlias, typer.Argument(help="Canonical report audit alias.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    payload = _resolve_command_payload(lambda: show_report_for_repo(repo, _report_audit(audit), _selected_project()))
    if _json_output(json_output):
        _echo_json(payload)
        return
    try:
        typer.echo(_report_markdown(payload))
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
    ] = DEFAULT_REPO_SORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: list_project_inventory(_selected_project(), sort),
        _json_output(json_output),
        _echo_repo_score_table,
    )


@repo_app.command("resolve", help="Resolve an Enji repo id or GitHub owner/name selector.")
def repo_resolve(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: resolve_repo(repo, _selected_project()),
        _json_output(json_output),
        _echo_repo_resolve_table,
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
        _echo_schedule_settings_table,
    )


@schedule_app.command("set", help="Batch update automatic report audit schedules.")
def schedule_set(
    repo: Annotated[
        str | None,
        typer.Argument(help="Optional repo id or owner/name. Without REPO, pass --project to batch one project."),
    ] = None,
    enabled: Annotated[
        Literal["on", "off", "keep"],
        typer.Option("--enabled", help="Enable or disable automatic scheduled checks."),
    ] = "keep",
    frequency: Annotated[
        Literal["daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"] | None,
        typer.Option("--freq", help="Schedule frequency."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: set_schedule_settings(
            repo,
            _selected_project(),
            ScheduleSettingsUpdate(
                enabled=_preference_switch(enabled),
                frequency=frequency,
                days_of_week=None,
                schedule_time=None,
                timezone=None,
            ),
        ),
        _json_output(json_output),
        _echo_schedule_settings_table,
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
        _echo_email_preferences_table,
    )


@email_app.command("set", help="Batch update report email preferences.")
def email_set(
    repo: Annotated[
        str | None,
        typer.Argument(help="Optional repo id or owner/name. Defaults to every repo in scope."),
    ] = None,
    manual: Annotated[
        Literal["on", "off", "keep"],
        typer.Option("--manual", help="Email after manual checks."),
    ] = "keep",
    auto: Annotated[
        Literal["on", "off", "keep"],
        typer.Option("--auto", help="Email after automatic scheduled checks."),
    ] = "keep",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _run_human_or_json_command(
        lambda: set_email_preferences(
            repo,
            _selected_project(),
            EmailPreferenceUpdate(
                manual_run_completion=_preference_switch(manual),
                scheduled_run_completion=_preference_switch(auto),
            ),
        ),
        _json_output(json_output),
        _echo_email_preferences_table,
    )


@catalog_app.command("audits", help=CATALOG_AUDITS_OPERATION.summary)
def catalog_audits(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    payload = resolve_operation_result(CATALOG_AUDITS_OPERATION.execute())
    if _json_output(json_output):
        _echo_json(payload)
        return
    _echo_audit_catalog(payload)


@catalog_app.command("audit", help=CATALOG_AUDIT_OPERATION.summary)
def catalog_audit(
    audit: Annotated[AuditAlias, typer.Argument(help="Canonical audit alias.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    payload = resolve_operation_result(CATALOG_AUDIT_OPERATION.execute(audit))
    if _json_output(json_output):
        _echo_json(payload)
        return
    _echo_key_values(_object_dict(payload))


@auth_app.command("import-cookie", help="Import a raw browser Cookie header from stdin.")
def auth_import_cookie(
    stdin: Annotated[bool, typer.Option("--stdin", help="Read a raw Cookie header from stdin.")] = False,
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ENJI_GUARD_AUTH_FILE or XDG config."),
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
            _echo_json(payload)
        else:
            _echo_key_values(cast(dict[str, object], payload))
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
        typer.Option("--auth-file", help="Auth file path. Defaults to ENJI_GUARD_AUTH_FILE or XDG config."),
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
            _echo_json(payload)
        else:
            _echo_key_values(cast(dict[str, object], payload))
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
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    payload = cast_auth_status_payload(resolve_operation_result(auth_status(auth_file)))
    if _json_output(json_output):
        _echo_json(payload)
    else:
        _echo_auth_status(payload)
    if not payload["authenticated"]:
        raise typer.Exit(3)


@auth_app.command("refresh", help="Refresh cookie auth and persist rotated cookies.")
def auth_refresh_command(
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ENJI_GUARD_AUTH_FILE or XDG config."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    try:
        payload = refresh_auth(auth_file)
        if _json_output(json_output):
            _echo_json(payload)
        else:
            _echo_key_values(cast(dict[str, object], payload))
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


def _preference_switch(value: Literal["on", "off", "keep"]) -> bool | None:
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
