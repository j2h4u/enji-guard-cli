"""Typer delivery adapter for the product-owned application surface.

The CLI is intentionally boring: command handlers validate command syntax,
call one typed :class:`~enji_guard_cli.application.Application` method, and
render the returned DTO.  No transport, gateway, or compatibility facade is
allowed to leak into this module.
"""

from __future__ import annotations

import json
import socket
import sys
from collections.abc import Callable
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from enji_guard_cli.application import (
    Application,
    ApplicationCatalogChange,
    ApplicationCommandError,
    ApplicationResult,
    AutofixWriteScope,
    EmailPreferencesUpdate,
)
from enji_guard_cli.composition import create_application
from enji_guard_cli.delivery.cli.presentation import FIELDS_PRESENTATION, CliPresentation, emit_text, json_projection
from enji_guard_cli.delivery.cli.presenters import (
    AUDIT_READ,
    AUDIT_SUMMARY,
    AUDIT_WAIT,
    AUTOFIX,
    EMAIL,
    GITLAB_CREDENTIALS,
    GITLAB_PROJECTS,
    OPERATION,
    PORTFOLIO,
    PROJECT_LIST,
    PROJECT_SETTINGS,
    REPOSITORY_STATUS,
    SCHEDULE,
)
from enji_guard_cli.delivery.mcp.server import create_mcp_server, run_mcp_server_async
from enji_guard_cli.mcp_facade import McpQueryFacade
from enji_guard_cli.runtime_observability.journey import AgentJourney, run_agent_journey
from enji_guard_cli.runtime_observability.readiness import readiness_verdict
from enji_guard_cli.runtime_observability.supervisor import run_service
from enji_guard_cli.runtime_observability.telemetry import configure_logging
from enji_guard_cli.settings import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    DEFAULT_MCP_TRANSPORT,
    RepositorySortName,
    default_settings,
)
from enji_guard_cli.version import version_text

app = typer.Typer(help="Agent-oriented Enji Guard portfolio and audit CLI.")
auth_app = typer.Typer(help="Manage Enji authentication.")
project_app = typer.Typer(help="Manage projects and project repositories.")
repo_app = typer.Typer(help="Manage connected repositories.")
recon_app = typer.Typer(help="Run baseline repository discovery (separate from audits).")
audit_app = typer.Typer(help="Read and run repository audits.")
portfolio_app = typer.Typer(help="Read portfolio status.")
schedule_app = typer.Typer(help="Manage automatic audit schedules.")
autofix_app = typer.Typer(help="Manage curated improvement jobs.")
email_app = typer.Typer(help="Manage audit completion email preferences.")
language_app = typer.Typer(help="Manage the account-wide audit language.")
gitlab_app = typer.Typer(help="Discover GitLab credentials and projects.")

for group, name in (
    (auth_app, "auth"),
    (project_app, "project"),
    (repo_app, "repo"),
    (recon_app, "recon"),
    (audit_app, "audit"),
    (portfolio_app, "portfolio"),
    (schedule_app, "schedule"),
    (autofix_app, "improvement-jobs"),
    (email_app, "email"),
    (language_app, "language"),
    (gitlab_app, "gitlab"),
):
    app.add_typer(group, name=name)

_state: dict[str, object] = {
    "project": None,
    "json": False,
    "auth_file": None,
    "operation": "cli",
    "application": None,
    "application_auth_file": None,
}


def _version_callback(value: bool) -> None:
    if not value:
        return
    typer.echo(version_text())
    raise typer.Exit


def _close_cached_application() -> None:
    cached = _state.get("application")
    try:
        if isinstance(cached, Application):
            cached.close()
    finally:
        _state["application"] = None
        _state["application_auth_file"] = None


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version and source commit."),
    ] = False,
    project: Annotated[str | None, typer.Option("--project", help="Exact project id or name filter.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
    auth_file: Annotated[Path | None, typer.Option("--auth-file", hidden=True)] = None,
) -> None:
    del version
    _close_cached_application()
    _state["project"] = project
    _state["json"] = json_output
    _state["auth_file"] = auth_file
    _state["application"] = None
    _state["application_auth_file"] = None
    _state["operation"] = f"cli {ctx.invoked_subcommand or 'root'}"
    # Click invokes registered close callbacks after command success or
    # failure, including the long-running ``run`` command after its supervisor
    # exits.  This keeps the pooled transport scoped to one CLI invocation.
    ctx.call_on_close(_close_cached_application)
    # The callback is the single CLI process entrypoint.  Explicit settings
    # ensure the default persistent telemetry path is honored even in tests.
    if ctx.invoked_subcommand != "run":
        configure_logging(default_settings().telemetry, provenance="cli")


def _configure_group_operation(group_name: str) -> Callable[[typer.Context], None]:
    def _callback(ctx: typer.Context) -> None:
        _state["operation"] = f"cli {group_name} {ctx.invoked_subcommand or 'root'}"

    return _callback


for _group_name, _group in (
    ("auth", auth_app),
    ("project", project_app),
    ("repo", repo_app),
    ("recon", recon_app),
    ("audit", audit_app),
    ("portfolio", portfolio_app),
    ("schedule", schedule_app),
    ("improvement-jobs", autofix_app),
    ("email", email_app),
    ("language", language_app),
    ("gitlab", gitlab_app),
):
    _group.callback()(_configure_group_operation(_group_name))


def _selected_project(local: str | None = None) -> str | None:
    if local is not None:
        return local
    value = _state["project"]
    return value if isinstance(value, str) and value.strip() else None


def _json_output(local: bool = False) -> bool:
    return local or _state["json"] is True


def _repository_sort(value: str) -> RepositorySortName:
    allowed = {"default", "name", "weakest", "overall", "latest-audit"}
    if value not in allowed:
        raise typer.BadParameter(f"sort must be one of: {', '.join(sorted(allowed))}", param_hint="--sort")
    return cast(RepositorySortName, value)


def _application(auth_file: Path | None = None) -> Application:
    selected = auth_file if auth_file is not None else cast(Path | None, _state["auth_file"])
    cached = _state["application"]
    if isinstance(cached, Application) and _state["application_auth_file"] == selected:
        return cached
    application = create_application(selected)
    _state["application"] = application
    _state["application_auth_file"] = selected
    return application


def _json(value: object, *, preserve_mapping_nulls: bool = False) -> object:
    """Convert application DTOs to JSON-safe values without dynamic dispatch."""
    return json_projection(value, preserve_mapping_nulls=preserve_mapping_nulls)


def _emit(payload: object, as_json: bool) -> None:
    rendered = _json(payload)
    if as_json:
        typer.echo(json.dumps(rendered, indent=2, sort_keys=True))
        return
    if isinstance(rendered, dict):
        for key, value in rendered.items():
            if isinstance(value, (dict, list)):
                typer.echo(f"{key}: {json.dumps(value, sort_keys=True)}")
            else:
                typer.echo(f"{key}: {value}")
        return
    typer.echo(json.dumps(rendered, indent=2, sort_keys=True))


def _run[PayloadT](
    action: Callable[[], PayloadT],
    as_json: bool,
    presentation: CliPresentation[PayloadT],
) -> None:
    """Execute a command action and keep expected operator errors on stderr."""
    changes: list[ApplicationCatalogChange] = []
    operation = str(_state.get("operation") or "cli")
    result: ApplicationResult | None = None

    def _catalog_changed(items: tuple[object, ...]) -> None:
        changes.extend(item for item in items if isinstance(item, ApplicationCatalogChange))

    def _catalog_changes() -> tuple[object, ...]:
        return () if result is None else result.catalog_changes

    def _execute() -> ApplicationResult:
        nonlocal result
        result = _application().execute(action)
        return result

    journey = AgentJourney(
        event_prefix="cli_command",
        operation=operation,
        surface="cli",
        provenance="cli",
        json_output=as_json,
    )
    try:
        result = cast(
            ApplicationResult,
            run_agent_journey(
                _execute,
                journey,
                exit_code_for_exception=_command_exit_code,
                audit_catalog_change_renderer=_catalog_changed,
                audit_catalog_change_reader=_catalog_changes,
            ),
        )
    except ApplicationCommandError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(exc.exit_code) from None
    payload = cast(PayloadT, result.payload)
    if as_json:
        rendered = presentation.json(payload)
        _emit(_with_catalog_changes(rendered, changes) if changes else rendered, True)
    else:
        emit_text(presentation.human(payload))
        if changes:
            typer.echo(f"audit catalog changed: {'; '.join(_catalog_change_text(change) for change in changes)}")


def _with_catalog_changes(payload: object, changes: list[ApplicationCatalogChange]) -> object:
    rendered = [
        {
            "action_key": change.action_key,
            "changed_fields": list(change.changed_fields),
            "kind": change.kind,
        }
        for change in changes
    ]
    audit_catalog = {"changes": rendered}
    if isinstance(payload, dict):
        return {**payload, "audit_catalog": audit_catalog}
    if isinstance(payload, (list, tuple)):
        return {"items": payload, "audit_catalog": audit_catalog}
    return {"value": payload, "audit_catalog": audit_catalog}


def _command_exit_code(exc: Exception) -> int:
    return exc.exit_code if isinstance(exc, ApplicationCommandError) else 1


def _catalog_change_text(change: ApplicationCatalogChange) -> str:
    if change.kind == "added":
        selector = change.action_key.removeprefix("audit.")
        return f"added audit {selector}"
    if change.kind == "removed":
        selector = change.action_key.removeprefix("audit.")
        return f"removed audit {selector}"
    fields = ", ".join(f"{field}: catalog metadata changed" for field in change.changed_fields)
    return f"changed audit {change.action_key.removeprefix('audit.')} ({fields or 'catalog metadata'})"


def _switch(value: Literal["on", "off"] | None) -> bool | None:
    return True if value == "on" else False if value == "off" else None


def _parse_duration(value: str) -> int:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("duration cannot be empty")
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    suffix = normalized[-1]
    multiplier = multipliers.get(suffix, 1)
    amount = normalized[:-1] if suffix in multipliers else normalized
    if not amount.isdigit():
        raise ValueError("duration must be an integer optionally followed by s, m, h, or d")
    return int(amount) * multiplier


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_http_bind(host: str, transport: str, *, allow_external_host: bool) -> None:
    if transport == "stdio" or allow_external_host or _is_loopback_host(host):
        return
    typer.echo(
        "VALIDATION: HTTP MCP transports may only bind to loopback by default; "
        "pass --allow-external-host to bind externally",
        err=True,
    )
    raise typer.Exit(1)


def _scope(all_repos: bool, all_projects: bool) -> AutofixWriteScope:
    if all_repos and all_projects:
        typer.echo("VALIDATION: pass --all-repos or --all-projects, not both", err=True)
        raise typer.Exit(1)
    return AutofixWriteScope(all_repos=all_repos, all_projects=all_projects)


@auth_app.command("import-cookie")
def auth_import_cookie(
    stdin: Annotated[bool, typer.Option("--stdin", help="Read the raw Cookie header from stdin.")] = False,
    auth_file: Annotated[Path | None, typer.Option("--auth-file", hidden=True)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not stdin:
        typer.echo("VALIDATION: use --stdin to avoid storing cookies in shell history", err=True)
        raise typer.Exit(1)
    raw_cookie = sys.stdin.read()
    _run(lambda: _application(auth_file).import_cookie(raw_cookie), _json_output(json_output), FIELDS_PRESENTATION)


@auth_app.command("import-bearer")
def auth_import_bearer(
    stdin: Annotated[bool, typer.Option("--stdin", help="Read the bearer or API token from stdin.")] = False,
    auth_file: Annotated[Path | None, typer.Option("--auth-file", hidden=True)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not stdin:
        typer.echo("VALIDATION: use --stdin to avoid storing tokens in shell history", err=True)
        raise typer.Exit(1)
    raw_token = sys.stdin.read()
    _run(lambda: _application(auth_file).import_bearer(raw_token), _json_output(json_output), FIELDS_PRESENTATION)


@auth_app.command("status")
def auth_status(
    auth_file: Annotated[Path | None, typer.Option("--auth-file", hidden=True)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application(auth_file).auth_status(), _json_output(json_output), FIELDS_PRESENTATION)


@project_app.command("list")
def project_list(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().list_projects(), _json_output(json_output), PROJECT_LIST)


@gitlab_app.command("credentials")
def gitlab_credentials(
    scope_type: Annotated[str | None, typer.Option("--scope-type")] = None,
    scope_owner: Annotated[str | None, typer.Option("--scope-owner")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1)] = 50,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().gitlab_credentials(
            scope_type=scope_type,
            scope_owner=scope_owner,
            limit=limit,
            offset=offset,
        ),
        _json_output(json_output),
        GITLAB_CREDENTIALS,
    )


@gitlab_app.command("projects")
def gitlab_projects(  # noqa: PLR0913
    *,
    credential_id: Annotated[str | None, typer.Option("--credential-id")] = None,
    search: Annotated[str | None, typer.Option("--search")] = None,
    page: Annotated[int, typer.Option("--page", min=1)] = 1,
    per_page: Annotated[int, typer.Option("--per-page", min=1)] = 50,
    all_pages: Annotated[bool, typer.Option("--all-pages", "--all")] = False,
    scope_type: Annotated[str | None, typer.Option("--scope-type")] = None,
    scope_owner: Annotated[str | None, typer.Option("--scope-owner")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().gitlab_projects(
            credential_id=credential_id,
            search=search,
            page=page,
            per_page=per_page,
            all_pages=all_pages,
            scope_type=scope_type,
            scope_owner=scope_owner,
        ),
        _json_output(json_output),
        GITLAB_PROJECTS,
    )


@project_app.command("create")
def project_create(name: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().create_project(name), _json_output(json_output), FIELDS_PRESENTATION)


@project_app.command("rename")
def project_rename(project: str, name: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().rename_project(project, name), _json_output(json_output), FIELDS_PRESENTATION)


@project_app.command("delete")
def project_delete(project: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().delete_project(project), _json_output(json_output), FIELDS_PRESENTATION)


@project_app.command("settings")
def project_settings(
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().project_settings(_selected_project(project)),
        _json_output(json_output),
        PROJECT_SETTINGS,
    )


@repo_app.command("list")
def repo_list(
    sort: Annotated[str, typer.Option("--sort")] = "default",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().portfolio_overview(_selected_project(), _repository_sort(sort)),
        _json_output(json_output),
        PORTFOLIO,
    )


@repo_app.command("resolve")
def repo_resolve(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().resolve_repository(repo, _selected_project(project)),
        _json_output(json_output),
        FIELDS_PRESENTATION,
    )


@repo_app.command("add")
def repo_add(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    repo_access_credential_id: Annotated[str | None, typer.Option("--repo-access-credential-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().add_repository(repo, _selected_project(project), repo_access_credential_id),
        _json_output(json_output),
        OPERATION,
    )


@repo_app.command("remove")
def repo_remove(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().remove_repository(repo, _selected_project(project)),
        _json_output(json_output),
        FIELDS_PRESENTATION,
    )


@repo_app.command("move")
def repo_move(
    repo: str,
    to_project: Annotated[str, typer.Option("--to-project")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().move_repository(repo, _selected_project(project), to_project),
        _json_output(json_output),
        OPERATION,
    )


@repo_app.command("status")
def repo_status(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().repository_status(repo, _selected_project(project)),
        _json_output(json_output),
        REPOSITORY_STATUS,
    )


@recon_app.command("start")
def recon_start(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().recon_start(repo, _selected_project(project)),
        _json_output(json_output),
        FIELDS_PRESENTATION,
    )


@recon_app.command("status")
def recon_status(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().repository_status(repo, _selected_project(project)),
        _json_output(json_output),
        REPOSITORY_STATUS,
    )


def _audit_selectors(audits: list[str] | None) -> list[str]:
    return [item.removeprefix("audit.") for item in (audits or [])]


@audit_app.command("start")
def audit_start(
    repo: str,
    audits: Annotated[list[str] | None, typer.Argument(help="Audit selector suffixes.")] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_audits: Annotated[bool, typer.Option("--all", help="Start every published audit.")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().audit_start(
            repo,
            _selected_project(project),
            _audit_selectors(audits),
            all_audits=all_audits,
        ),
        _json_output(json_output),
        OPERATION,
    )


@audit_app.command("read")
def audit_read(
    repo: str,
    audits: Annotated[list[str] | None, typer.Argument(help="Audit selector suffixes.")] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_audits: Annotated[bool, typer.Option("--all")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().audit_read(
            repo,
            _audit_selectors(audits),
            project=_selected_project(project),
            all_audits=all_audits,
        ),
        _json_output(json_output),
        AUDIT_READ,
    )


@audit_app.command("summary")
def audit_summary(
    repo: str,
    audits: Annotated[list[str] | None, typer.Argument(help="Optional audit selector suffixes.")] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_audits: Annotated[bool, typer.Option("--all")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    selectors = _audit_selectors(audits)
    if all_audits and selectors:
        typer.echo("VALIDATION: pass audit selectors or --all, not both", err=True)
        raise typer.Exit(1)
    selected = [] if all_audits else selectors
    _run(
        lambda: _application().audit_summary(repo, selected, project=_selected_project(project)),
        _json_output(json_output),
        AUDIT_SUMMARY,
    )


@audit_app.command("status")
def audit_status(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().repository_status(repo, _selected_project(project)),
        _json_output(json_output),
        REPOSITORY_STATUS,
    )


@audit_app.command("wait")
def audit_wait(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    timeout: Annotated[str, typer.Option("--timeout")] = default_settings().audit_wait.timeout_text,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().audit_wait(
            repo, project=_selected_project(project), timeout_seconds=_parse_duration(timeout)
        ),
        _json_output(json_output),
        AUDIT_WAIT,
    )


@portfolio_app.command("status")
def portfolio_status(
    sort: Annotated[str, typer.Option("--sort")] = "default",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().portfolio_overview(_selected_project(), _repository_sort(sort)),
        _json_output(json_output),
        PORTFOLIO,
    )


@app.command("health")
def health(
    ready: Annotated[bool, typer.Option("--ready", help="Check MCP listener and cached backend readiness.")] = False,
    host: Annotated[str, typer.Option("--host")] = DEFAULT_HTTP_HOST,
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = DEFAULT_HTTP_PORT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not ready:
        _emit({"status": "ok"}, _json_output(json_output))
        return
    try:
        with socket.create_connection((host, port), timeout=default_settings().service.local_readiness_timeout_seconds):
            pass
    except OSError as exc:
        typer.echo(f"UNREADY: MCP listener is not ready: {exc}", err=True)
        raise typer.Exit(1) from None
    verdict = readiness_verdict()
    if not verdict.ready:
        reason = verdict.reason or "backend readiness failed"
        if verdict.state is not None and verdict.state.failure_code is not None:
            reason = f"{reason}: {verdict.state.failure_code}"
        typer.echo(f"UNREADY: {reason}", err=True)
        raise typer.Exit(1)
    _emit({"status": "ready"}, _json_output(json_output))


@app.command("access")
def access(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().access(), _json_output(json_output), FIELDS_PRESENTATION)


@app.command("run")
def run(
    transport: Annotated[
        Literal["stdio", "sse", "streamable-http"], typer.Option("--transport")
    ] = DEFAULT_MCP_TRANSPORT,
    host: Annotated[str, typer.Option("--host")] = DEFAULT_HTTP_HOST,
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = DEFAULT_HTTP_PORT,
    mount_path: Annotated[str | None, typer.Option("--mount-path")] = None,
    allow_external_host: Annotated[bool, typer.Option("--allow-external-host")] = False,
) -> None:
    _validate_http_bind(host, transport, allow_external_host=allow_external_host)
    application = _application()
    run_service(
        transport=transport,
        host=host,
        port=port,
        mount_path=mount_path,
        runtime_auth=application.runtime_auth_port(),
        mcp_server_factory=lambda host, port: create_mcp_server(host, port, queries=McpQueryFacade(application)),
        mcp_server_runner=run_mcp_server_async,
        settings=default_settings(),
    )


@app.command("status")
def status(
    repo: Annotated[str | None, typer.Argument()] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    sort: Annotated[str, typer.Option("--sort")] = "default",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if repo is not None:
        _run(
            lambda: _application().repository_status(repo, _selected_project(project)),
            _json_output(json_output),
            REPOSITORY_STATUS,
        )
        return
    _run(
        lambda: _application().portfolio_overview(_selected_project(project), _repository_sort(sort)),
        _json_output(json_output),
        PORTFOLIO,
    )


@app.command("wait")
def wait(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    timeout: Annotated[str, typer.Option("--timeout")] = default_settings().audit_wait.timeout_text,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    audit_wait(repo, project, timeout, json_output)


@schedule_app.command("list")
def schedule_list(
    repo: str | None = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().list_schedules(repo, _selected_project(project)),
        _json_output(json_output),
        SCHEDULE,
    )


@schedule_app.command("set")
def schedule_set(  # noqa: PLR0913
    *,
    repo: str | None = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
    all_projects: Annotated[bool, typer.Option("--all-projects")] = False,
    enabled: Annotated[Literal["on", "off"] | None, typer.Option("--enabled")] = None,
    frequency: Annotated[str | None, typer.Option("--frequency")] = None,
    timezone: Annotated[str | None, typer.Option("--timezone")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    scope = _scope(all_repos, all_projects)
    _run(
        lambda: _application().set_schedules(
            repo,
            _selected_project(project),
            enabled=_switch(enabled),
            cadence=frequency,
            timezone=timezone,
            scope=scope,
        ),
        _json_output(json_output),
        OPERATION,
    )


@schedule_app.command("auto-time")
def schedule_auto_time(
    repo: str | None = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
    all_projects: Annotated[bool, typer.Option("--all-projects")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    scope = _scope(all_repos, all_projects)
    _run(
        lambda: _application().schedule_auto_time(repo, _selected_project(project), scope=scope),
        _json_output(json_output),
        OPERATION,
    )


@schedule_app.command("timezone")
def schedule_timezone(  # noqa: PLR0913
    *,
    timezone: str,
    repo: str | None = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
    all_projects: Annotated[bool, typer.Option("--all-projects")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    scope = _scope(all_repos, all_projects)
    _run(
        lambda: _application().set_schedules(repo, _selected_project(project), timezone=timezone, scope=scope),
        _json_output(json_output),
        OPERATION,
    )


@autofix_app.command("list")
def autofix_list(
    repo: str | None = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().list_autofixes(repo, _selected_project(project)),
        _json_output(json_output),
        AUTOFIX,
    )


@autofix_app.command("set")
def autofix_set(  # noqa: PLR0913
    *,
    repo: str | None = None,
    autofixes: Annotated[list[str] | None, typer.Argument(help="Autofix selectors.")] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_autofixes: Annotated[bool, typer.Option("--all")] = False,
    all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
    all_projects: Annotated[bool, typer.Option("--all-projects")] = False,
    enabled: Annotated[Literal["on", "off"] | None, typer.Option("--enabled")] = None,
    frequency: Annotated[str | None, typer.Option("--frequency")] = None,
    timezone: Annotated[str | None, typer.Option("--timezone")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    selectors = ["__all__"] if all_autofixes else (autofixes or [])
    scope = _scope(all_repos, all_projects)
    _run(
        lambda: _application().set_autofixes(
            repo,
            _selected_project(project),
            selectors,
            enabled=_switch(enabled),
            cadence=frequency,
            timezone=timezone,
            scope=scope,
        ),
        _json_output(json_output),
        OPERATION,
    )


@email_app.command("list")
def email_list(
    repo: str | None = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().list_email_preferences(repo, _selected_project(project)),
        _json_output(json_output),
        EMAIL,
    )


@email_app.command("set")
def email_set(  # noqa: PLR0913
    *,
    repo: str | None = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
    all_projects: Annotated[bool, typer.Option("--all-projects")] = False,
    manual: Annotated[Literal["on", "off"] | None, typer.Option("--manual")] = None,
    scheduled: Annotated[Literal["on", "off"] | None, typer.Option("--scheduled")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    scope = _scope(all_repos, all_projects)
    update = EmailPreferencesUpdate(_switch(manual), _switch(scheduled))
    _run(
        lambda: _application().set_email_preferences(repo, _selected_project(project), update, scope=scope),
        _json_output(json_output),
        EMAIL,
    )


@language_app.command("show")
def language_show(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().language(), _json_output(json_output), FIELDS_PRESENTATION)


@language_app.command("set")
def language_set(
    language: Annotated[Literal["en", "ru"], typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().set_language(language), _json_output(json_output), FIELDS_PRESENTATION)


__all__ = ["app"]
