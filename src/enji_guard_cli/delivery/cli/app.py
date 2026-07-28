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
    AUDIT_CADENCES,
    Application,
    ApplicationCatalogChange,
    ApplicationCommandError,
    ApplicationResult,
    AutofixWriteScope,
)
from enji_guard_cli.composition import create_application, runtime_auth_service
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
from enji_guard_cli.runtime_observability.journey import AgentJourney, run_agent_journey
from enji_guard_cli.runtime_observability.readiness import readiness_verdict
from enji_guard_cli.runtime_observability.supervisor import RuntimeServiceOptions, run_service
from enji_guard_cli.runtime_observability.telemetry import configure_logging
from enji_guard_cli.settings import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    DEFAULT_MCP_TRANSPORT,
    REPOSITORY_SORT_NAMES,
    RepositorySortName,
    default_settings,
)
from enji_guard_cli.version import version_text

ROOT_HELP = """Agent-oriented Enji Guard portfolio and audit CLI.

Mental model:
- status REPO: one fresh snapshot for readiness, freshness, and running audits.
- audit summary REPO: compact metadata for already available reports.
- audit read REPO AUDIT: read one report body.
- wait REPO: block until audits finish; do not use short timeouts as refresh.

For "are audits ready?", run status REPO first and stop there unless it shows a
real anomaly.
"""

AUDIT_HELP = """Read and run repository audits.

Use status REPO as the first readiness check. audit summary and audit read are
for available reports. wait is a real blocking wait after status, not a refresh
mechanism.
"""

app = typer.Typer(help=ROOT_HELP, invoke_without_command=True)
auth_app = typer.Typer(help="Manage Enji authentication.")
project_app = typer.Typer(help="Manage projects and project repositories.")
repo_app = typer.Typer(help="Manage connected repositories. To read them, use 'status' (all) or 'status REPO' (one).")
recon_app = typer.Typer(help="Run baseline repository discovery (separate from audits).")
audit_app = typer.Typer(help=AUDIT_HELP)
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
            cached.runner.close()
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
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit
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


def _fail(code: str, message: str, *, as_json: bool, exit_code: int = 1) -> typer.Exit:
    """Render one operator error on stderr and return the matching exit request.

    ``--json`` callers get a stable ``{"code", "message"}`` envelope so that no
    automation has to regex-parse the human ``CODE: message`` line.
    """
    if as_json:
        typer.echo(json.dumps({"code": code, "message": message}, indent=2, sort_keys=True), err=True)
    else:
        typer.echo(f"{code}: {message}", err=True)
    return typer.Exit(exit_code)


SORT_HELP = f"Repository order: {', '.join(sorted(REPOSITORY_SORT_NAMES))}."

FREQUENCY_HELP = f"Run cadence: {', '.join(AUDIT_CADENCES)}."

TIMEZONE_HELP = "IANA timezone stored with each subscription, such as Asia/Almaty."

ENABLED_HELP = "Turn the subscription on or off."


def _repository_sort(value: str) -> RepositorySortName:
    if value not in REPOSITORY_SORT_NAMES:
        raise typer.BadParameter(
            f"sort must be one of: {', '.join(sorted(REPOSITORY_SORT_NAMES))}", param_hint="--sort"
        )
    return cast(RepositorySortName, value)


def _application(auth_file: Path | None = None) -> Application:
    selected = auth_file if auth_file is not None else cast(Path | None, _state["auth_file"])
    cached = _state["application"]
    if isinstance(cached, Application) and _state["application_auth_file"] == selected:
        return cached
    # A single invocation can ask for two different credential files: ``_run``
    # resolves the global one while the command action passes the subcommand's
    # ``--auth-file``.  The cache holds exactly one application, so the one being
    # displaced must be closed here or its pooled client is orphaned --
    # ``_close_cached_application`` would only ever see the survivor.
    _close_cached_application()
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
        result = _application().runner.execute(action)
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
        raise _fail(exc.code, _operator_message(exc), as_json=as_json, exit_code=exc.exit_code) from None
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


def _operator_message(exc: ApplicationCommandError) -> str:
    """Render one command failure for an operator terminal.

    Credential failures are a dead end without the file path and the exact
    import commands, so the CLI — and only the CLI — appends them here.  MCP
    renders the same error without any host path or shell instruction.
    """
    if not exc.code.startswith("AUTH_"):
        return exc.message
    return f"{exc.message}. {_auth_remediation(_selected_credential_location())}"


def _selected_credential_location() -> Path:
    """Resolve the credential file this invocation asked for."""
    selected = cast(Path | None, _state["auth_file"])
    return selected if selected is not None else default_settings().auth.auth_file


def _auth_remediation(credential_location: Path) -> str:
    """Name the credential file and the exact commands that repair first run."""
    return (
        f"Credential file: {credential_location}. "
        "First run: mkdir -p ~/.config/enji-guard/logs && chmod 700 ~/.config/enji-guard, then import a "
        "credential with: printf '%s' \"$ENJI_API_TOKEN\" | enji-guard auth import-bearer --stdin "
        "(cookie auth: enji-guard auth import-cookie --stdin). "
        "Verify with: enji-guard auth status"
    )


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
    raise _fail(
        "VALIDATION",
        "HTTP MCP transports may only bind to loopback by default; pass --allow-external-host to bind externally",
        as_json=_json_output(),
    )


ALL_PROJECTS_WARNING = "--all-projects rewrites this setting for every repository in every project of the account"


def _is_interactive() -> bool:
    """Report whether a human can answer a prompt on this stdin."""
    return sys.stdin.isatty()


CONFIRM_HELP = "Confirm this irreversible deletion without prompting; required when not a TTY."


def _confirm_deletion(warning: str, *, as_json: bool, assume_yes: bool) -> None:
    """Gate an irreversible single-target deletion behind the ``--yes`` contract.

    Same rule as the ``--all-projects`` blast radius: a human on a TTY is asked,
    while agents, MCP, CI, and any ``--json`` caller are never prompted and must
    pass ``--yes``, so a non-interactive run fails fast instead of hanging on a
    prompt nobody can answer.
    """
    if assume_yes:
        return
    if as_json or not _is_interactive():
        raise _fail("CONFIRMATION_REQUIRED", f"{warning}; re-run with --yes to confirm", as_json=as_json)
    if not typer.confirm(f"{warning}. Continue?"):
        raise _fail("ABORTED", "no change was made", as_json=as_json)


REPO_SCOPE_HELP = "Write to one repository; mutually exclusive with --all-repos and --all-projects."

REPO_FILTER_HELP = "Read one repository; omit to read every repository in scope."

SCOPE_REQUIRED = "pass --repo REPO, --all-repos with --project, or --all-projects"


def _scope(
    all_repos: bool,
    all_projects: bool,
    *,
    repo: str | None = None,
    as_json: bool = False,
    assume_yes: bool = False,
) -> AutofixWriteScope:
    """Validate write scope and gate the unbounded --all-projects blast radius.

    Interactive operators are asked to confirm; agents, MCP, CI, and any
    ``--json`` caller are never prompted and must pass ``--yes`` instead, so a
    non-TTY invocation can fail fast rather than block on a hidden prompt.
    """
    if all_repos and all_projects:
        raise _fail("VALIDATION", "pass --all-repos or --all-projects, not both", as_json=as_json)
    if repo is not None and (all_repos or all_projects):
        raise _fail("VALIDATION", "--repo cannot be combined with --all-repos or --all-projects", as_json=as_json)
    if repo is None and not all_repos and not all_projects:
        raise _fail("VALIDATION", SCOPE_REQUIRED, as_json=as_json)
    if all_projects and not assume_yes:
        if as_json or not _is_interactive():
            raise _fail(
                "CONFIRMATION_REQUIRED",
                f"{ALL_PROJECTS_WARNING}; re-run with --yes to confirm",
                as_json=as_json,
            )
        if not typer.confirm(f"{ALL_PROJECTS_WARNING}. Continue?"):
            raise _fail("ABORTED", "no change was made", as_json=as_json)
    return AutofixWriteScope(all_repos=all_repos, all_projects=all_projects)


@auth_app.command("import-cookie")
def auth_import_cookie(
    stdin: Annotated[bool, typer.Option("--stdin", help="Read the raw Cookie header from stdin.")] = False,
    auth_file: Annotated[Path | None, typer.Option("--auth-file", hidden=True)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not stdin:
        raise _fail(
            "VALIDATION", "use --stdin to avoid storing cookies in shell history", as_json=_json_output(json_output)
        )
    raw_cookie = sys.stdin.read()
    _run(lambda: _application(auth_file).auth.import_cookie(raw_cookie), _json_output(json_output), FIELDS_PRESENTATION)


@auth_app.command("import-bearer")
def auth_import_bearer(
    stdin: Annotated[bool, typer.Option("--stdin", help="Read the bearer or API token from stdin.")] = False,
    auth_file: Annotated[Path | None, typer.Option("--auth-file", hidden=True)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not stdin:
        raise _fail(
            "VALIDATION", "use --stdin to avoid storing tokens in shell history", as_json=_json_output(json_output)
        )
    raw_token = sys.stdin.read()
    _run(lambda: _application(auth_file).auth.import_bearer(raw_token), _json_output(json_output), FIELDS_PRESENTATION)


@auth_app.command("status")
def auth_status(
    auth_file: Annotated[Path | None, typer.Option("--auth-file", hidden=True)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application(auth_file).auth.auth_status(), _json_output(json_output), FIELDS_PRESENTATION)


@project_app.command("list")
def project_list(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().portfolio.list_projects(), _json_output(json_output), PROJECT_LIST)


@gitlab_app.command("credentials")
def gitlab_credentials(
    scope_type: Annotated[str | None, typer.Option("--scope-type")] = None,
    scope_owner: Annotated[str | None, typer.Option("--scope-owner")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1)] = 50,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().gitlab.gitlab_credentials(
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
        lambda: _application().gitlab.gitlab_projects(
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
    _run(lambda: _application().portfolio.create_project(name), _json_output(json_output), FIELDS_PRESENTATION)


@project_app.command("rename")
def project_rename(project: str, name: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().portfolio.rename_project(project, name), _json_output(json_output), FIELDS_PRESENTATION)


@project_app.command("delete")
def project_delete(
    project: str,
    yes: Annotated[bool, typer.Option("--yes", help=CONFIRM_HELP)] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _confirm_deletion(
        f"deleting project {project} is permanent and cannot be undone",
        as_json=_json_output(json_output),
        assume_yes=yes,
    )
    _run(lambda: _application().portfolio.delete_project(project), _json_output(json_output), FIELDS_PRESENTATION)


@project_app.command("settings")
def project_settings(
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().portfolio.project_settings(_selected_project(project)),
        _json_output(json_output),
        PROJECT_SETTINGS,
    )


@repo_app.command("resolve")
def repo_resolve(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().portfolio.resolve_repository(repo, _selected_project(project)),
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
        lambda: _application().portfolio.add_repository(repo, _selected_project(project), repo_access_credential_id),
        _json_output(json_output),
        OPERATION,
    )


@repo_app.command("remove")
def repo_remove(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    yes: Annotated[bool, typer.Option("--yes", help=CONFIRM_HELP)] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _confirm_deletion(
        f"detaching {repo} makes its accumulated audit history unreachable",
        as_json=_json_output(json_output),
        assume_yes=yes,
    )
    _run(
        lambda: _application().portfolio.remove_repository(repo, _selected_project(project)),
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
        lambda: _application().portfolio.move_repository(repo, _selected_project(project), to_project),
        _json_output(json_output),
        OPERATION,
    )


@recon_app.command("start")
def recon_start(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().portfolio.recon_start(repo, _selected_project(project)),
        _json_output(json_output),
        FIELDS_PRESENTATION,
    )


def _audit_selectors(audits: list[str] | None) -> list[str]:
    return [item.removeprefix("audit.") for item in (audits or [])]


def _explicit_audit_selectors(audits: list[str] | None, *, all_audits: bool, as_json: bool) -> list[str]:
    """Reject a selector list combined with --all instead of letting --all win."""
    selectors = _audit_selectors(audits)
    if all_audits and selectors:
        raise _fail("VALIDATION", "pass audit selectors or --all, not both", as_json=as_json)
    return selectors


@audit_app.command("start")
def audit_start(
    repo: str,
    audits: Annotated[list[str] | None, typer.Argument(help="Audit selector suffixes.")] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_audits: Annotated[bool, typer.Option("--all", help="Start every published audit.")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    selectors = _explicit_audit_selectors(audits, all_audits=all_audits, as_json=_json_output(json_output))
    _run(
        lambda: _application().audit.audit_start(
            repo,
            _selected_project(project),
            selectors,
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
    all_audits: Annotated[bool, typer.Option("--all", help="Read every published audit.")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    selectors = _explicit_audit_selectors(audits, all_audits=all_audits, as_json=_json_output(json_output))
    _run(
        lambda: _application().audit.audit_read(
            repo,
            selectors,
            project=_selected_project(project),
            all_audits=all_audits,
        ),
        _json_output(json_output),
        AUDIT_READ,
    )


@audit_app.command("summary")
def audit_summary(
    repo: str,
    audits: Annotated[
        list[str] | None,
        typer.Argument(help="Optional audit selector suffixes; omit to summarize every published audit."),
    ] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().audit.audit_summary(repo, _audit_selectors(audits), project=_selected_project(project)),
        _json_output(json_output),
        AUDIT_SUMMARY,
    )


@app.command(
    "health",
    help=(
        "Report process liveness. Pass --ready for the real dependency check: "
        "it probes the MCP listener and the cached backend readiness state, and "
        "is what container healthchecks and monitors must use."
    ),
)
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
        raise _fail("UNREADY", f"MCP listener is not ready: {exc}", as_json=_json_output(json_output)) from None
    verdict = readiness_verdict()
    if not verdict.ready:
        reason = verdict.reason or "backend readiness failed"
        if verdict.state is not None and verdict.state.failure_code is not None:
            reason = f"{reason}: {verdict.state.failure_code}"
        raise _fail("UNREADY", reason, as_json=_json_output(json_output))
    _emit({"status": "ready"}, _json_output(json_output))


@app.command("access", help="Show the account plan, limits, and entitlements.")
def access(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().portfolio.access(), _json_output(json_output), FIELDS_PRESENTATION)


@app.command(
    "run",
    help="Run the long-lived MCP service. This is the container entrypoint, not an operator command.",
)
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
    auth_file = cast(Path | None, _state["auth_file"])
    # Two independent lifetimes, nested deliberately.  The MCP server composes
    # and closes its own narrow read-only surface inside its lifespan, which
    # ends when the supervised MCP task finishes.  The credential coordinator
    # outlives it: this ``with`` closes only after ``run_service`` returns, so
    # the refresh loop keeps a live client throughout supervise_tasks shutdown,
    # and its pool is released on the success and the failure path alike.
    with runtime_auth_service(auth_file) as runtime_auth:
        run_service(
            options=RuntimeServiceOptions(transport=transport, host=host, port=port, mount_path=mount_path),
            runtime_auth=runtime_auth,
            mcp_server_factory=lambda host, port: create_mcp_server(host, port, auth_file=auth_file),
            mcp_server_runner=run_mcp_server_async,
            settings=default_settings(),
        )


@app.command("status", help="Show portfolio status, or one repository audit snapshot when REPO is provided.")
def status(
    repo: Annotated[str | None, typer.Argument()] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    sort: Annotated[str | None, typer.Option("--sort", help=SORT_HELP)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if repo is not None:
        _run(
            lambda: _application().portfolio.repository_status(repo, _selected_project(project)),
            _json_output(json_output),
            REPOSITORY_STATUS,
        )
        return
    _run(
        lambda: _application().portfolio.portfolio_overview(
            _selected_project(project), _repository_sort(sort or default_settings().repo.default_sort)
        ),
        _json_output(json_output),
        PORTFOLIO,
    )


@app.command("wait", help="Block until repository audits finish. Do not use short timeouts as refresh.")
def wait(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    timeout: Annotated[str, typer.Option("--timeout")] = default_settings().audit_wait.timeout_text,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().audit.audit_wait(
            repo, project=_selected_project(project), timeout_seconds=_parse_duration(timeout)
        ),
        _json_output(json_output),
        AUDIT_WAIT,
    )


@schedule_app.command("list")
def schedule_list(
    repo: Annotated[str | None, typer.Option("--repo", help=REPO_FILTER_HELP)] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().subscriptions.list_schedules(repo, _selected_project(project)),
        _json_output(json_output),
        SCHEDULE,
    )


@schedule_app.command("set")
def schedule_set(  # noqa: PLR0913
    *,
    repo: Annotated[str | None, typer.Option("--repo", help=REPO_SCOPE_HELP)] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
    all_projects: Annotated[
        bool, typer.Option("--all-projects", help="Every repository in every project; requires --yes when not a TTY.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm an --all-projects write without prompting.")] = False,
    enabled: Annotated[Literal["on", "off"] | None, typer.Option("--enabled", help=ENABLED_HELP)] = None,
    frequency: Annotated[str | None, typer.Option("--frequency", help=FREQUENCY_HELP)] = None,
    timezone: Annotated[str | None, typer.Option("--timezone", help=TIMEZONE_HELP)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    scope = _scope(all_repos, all_projects, repo=repo, as_json=_json_output(json_output), assume_yes=yes)
    _run(
        lambda: _application().subscriptions.set_schedules(
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
def schedule_auto_time(  # noqa: PLR0913
    *,
    repo: Annotated[str | None, typer.Option("--repo", help=REPO_SCOPE_HELP)] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
    all_projects: Annotated[
        bool, typer.Option("--all-projects", help="Every repository in every project; requires --yes when not a TTY.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm an --all-projects write without prompting.")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    scope = _scope(all_repos, all_projects, repo=repo, as_json=_json_output(json_output), assume_yes=yes)
    _run(
        lambda: _application().subscriptions.schedule_auto_time(repo, _selected_project(project), scope=scope),
        _json_output(json_output),
        OPERATION,
    )


@schedule_app.command("timezone")
def schedule_timezone(  # noqa: PLR0913
    *,
    timezone: Annotated[str, typer.Argument(help=TIMEZONE_HELP)],
    repo: Annotated[str | None, typer.Option("--repo", help=REPO_SCOPE_HELP)] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
    all_projects: Annotated[
        bool, typer.Option("--all-projects", help="Every repository in every project; requires --yes when not a TTY.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm an --all-projects write without prompting.")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    scope = _scope(all_repos, all_projects, repo=repo, as_json=_json_output(json_output), assume_yes=yes)
    _run(
        lambda: _application().subscriptions.set_schedules(
            repo, _selected_project(project), timezone=timezone, scope=scope
        ),
        _json_output(json_output),
        OPERATION,
    )


@autofix_app.command("list")
def autofix_list(
    repo: Annotated[str | None, typer.Option("--repo", help=REPO_FILTER_HELP)] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().subscriptions.list_autofixes(repo, _selected_project(project)),
        _json_output(json_output),
        AUTOFIX,
    )


@autofix_app.command("set")
def autofix_set(  # noqa: PLR0913
    *,
    repo: Annotated[str | None, typer.Option("--repo", help=REPO_SCOPE_HELP)] = None,
    autofixes: Annotated[list[str] | None, typer.Argument(help="Autofix selectors.")] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_autofixes: Annotated[bool, typer.Option("--all", help="Every supported autofix selector.")] = False,
    all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
    all_projects: Annotated[
        bool, typer.Option("--all-projects", help="Every repository in every project; requires --yes when not a TTY.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm an --all-projects write without prompting.")] = False,
    enabled: Annotated[Literal["on", "off"] | None, typer.Option("--enabled", help=ENABLED_HELP)] = None,
    frequency: Annotated[str | None, typer.Option("--frequency", help=FREQUENCY_HELP)] = None,
    timezone: Annotated[str | None, typer.Option("--timezone", help=TIMEZONE_HELP)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    selectors = ["__all__"] if all_autofixes else (autofixes or [])
    scope = _scope(all_repos, all_projects, repo=repo, as_json=_json_output(json_output), assume_yes=yes)
    _run(
        lambda: _application().subscriptions.set_autofixes(
            repo,
            _selected_project(project),
            selectors,
            enabled=_switch(enabled),
            frequency=frequency,
            timezone=timezone,
            scope=scope,
        ),
        _json_output(json_output),
        OPERATION,
    )


@email_app.command("list")
def email_list(
    repo: Annotated[str | None, typer.Option("--repo", help=REPO_FILTER_HELP)] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().subscriptions.list_email_preferences(repo, _selected_project(project)),
        _json_output(json_output),
        EMAIL,
    )


@email_app.command("set")
def email_set(  # noqa: PLR0913
    *,
    repo: Annotated[str | None, typer.Option("--repo", help=REPO_SCOPE_HELP)] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
    all_projects: Annotated[
        bool, typer.Option("--all-projects", help="Every repository in every project; requires --yes when not a TTY.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm an --all-projects write without prompting.")] = False,
    manual: Annotated[
        Literal["on", "off"] | None, typer.Option("--manual", help="Email on manually started audit runs.")
    ] = None,
    scheduled: Annotated[
        Literal["on", "off"] | None, typer.Option("--scheduled", help="Email on scheduled audit runs.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    scope = _scope(all_repos, all_projects, repo=repo, as_json=_json_output(json_output), assume_yes=yes)
    _run(
        lambda: _application().subscriptions.set_email_preferences(
            repo, _selected_project(project), manual=_switch(manual), scheduled=_switch(scheduled), scope=scope
        ),
        _json_output(json_output),
        EMAIL,
    )


@language_app.command("show")
def language_show(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().portfolio.language(), _json_output(json_output), FIELDS_PRESENTATION)


@language_app.command("set")
def language_set(
    language: Annotated[Literal["en", "ru"], typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().portfolio.set_language(language), _json_output(json_output), FIELDS_PRESENTATION)


__all__ = ["app"]
