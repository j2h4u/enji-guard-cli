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
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from enji_guard_cli.application import Application, AutofixWriteScope, EmailPreferencesUpdate
from enji_guard_cli.audit.artifacts import AuditArtifactUnavailableError
from enji_guard_cli.audit.errors import AuditMalformedError, AuditNotFoundError, AuditUpstreamError
from enji_guard_cli.audit.ports import (
    AuditAutofixUpdate,
    AuditCatalogChange,
    AuditScheduleUpdate,
    AuditWaitOptions,
    MalformedAuditSnapshotError,
)
from enji_guard_cli.auth_session.api import AuthError
from enji_guard_cli.enji_gateway.catalog_snapshot import (
    begin_audit_catalog_observation,
    end_audit_catalog_observation,
)
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.portfolio.errors import PortfolioMalformedError, PortfolioNotFoundError, PortfolioUpstreamError
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
):
    app.add_typer(group, name=name)

_state: dict[str, object] = {
    "project": None,
    "json": False,
    "auth_file": None,
    "operation": "cli",
    "application": None,
}


@app.callback()
def main(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option("--project", help="Exact project id or name filter.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
    auth_file: Annotated[Path | None, typer.Option("--auth-file", hidden=True)] = None,
) -> None:
    _state["project"] = project
    _state["json"] = json_output
    _state["auth_file"] = auth_file
    _state["application"] = None
    _state["operation"] = f"cli {ctx.invoked_subcommand or 'root'}"
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
    application = Application.from_auth_file(selected)
    _state["application"] = application
    return application


def _json(value: object) -> object:  # noqa: PLR0911
    """Convert application DTOs to JSON-safe values without dynamic dispatch."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _json(asdict(value))
    return str(value)


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


def _run(action: Callable[[], object], as_json: bool) -> None:  # noqa: C901
    """Execute a command action and keep expected operator errors on stderr."""
    changes: list[AuditCatalogChange] = []
    operation = str(_state.get("operation") or "cli")
    audit_aware = _is_audit_aware_operation(operation)

    def _catalog_changed(items: tuple[object, ...]) -> None:
        changes.extend(cast(AuditCatalogChange, item) for item in items)

    def _catalog_changes() -> tuple[object, ...]:
        if not audit_aware:
            return ()
        application = _state.get("application")
        reader = getattr(application, "catalog_observation", None)
        if not callable(reader):
            return ()
        observed = reader()
        typed_changes = getattr(observed, "changes", ())
        return tuple(typed_changes) if isinstance(typed_changes, tuple) else ()

    journey = AgentJourney(
        event_prefix="cli_command",
        operation=operation,
        surface="cli",
        provenance="cli",
        json_output=as_json,
    )
    observation_token = (
        begin_audit_catalog_observation(state_file=default_settings().audit_catalog.state_file) if audit_aware else None
    )
    try:
        try:
            payload = run_agent_journey(
                action,
                journey,
                exit_code_for_exception=lambda _exc: 1,
                audit_catalog_change_renderer=_catalog_changed,
                audit_catalog_change_reader=_catalog_changes,
            )
        finally:
            if observation_token is not None:
                end_audit_catalog_observation(observation_token)
    except EnjiApiError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(_exit_code_for_error(exc.code)) from None
    except AuthError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(3 if exc.code.startswith("AUTH_") else 1) from None
    except (AuditArtifactUnavailableError, AuditNotFoundError, PortfolioNotFoundError) as exc:
        typer.echo(f"NOT_FOUND: {exc}", err=True)
        raise typer.Exit(4) from None
    except (
        MalformedAuditSnapshotError,
        AuditMalformedError,
        AuditUpstreamError,
        PortfolioMalformedError,
        PortfolioUpstreamError,
    ) as exc:
        typer.echo(f"UPSTREAM: {exc}", err=True)
        raise typer.Exit(1) from None
    except (ValueError, OSError) as exc:
        typer.echo(f"VALIDATION: {exc}", err=True)
        raise typer.Exit(1) from None
    if as_json:
        _emit(_with_catalog_changes(payload, changes) if audit_aware else payload, True)
    else:
        _emit(payload, False)
        if changes:
            typer.echo(f"audit catalog changed: {'; '.join(_catalog_change_text(change) for change in changes)}")


def _exit_code_for_error(code: str) -> int:
    if code.startswith("AUTH_"):
        return 3
    if code in {"NOT_FOUND", "BAD_SELECTOR"}:
        return 4
    return 1


def _is_audit_aware_operation(operation: str) -> bool:
    # Keep this list aligned with command handlers whose Application method
    # actually reads the audit catalog.  Portfolio mutations and selectors do
    # not need an observation lifecycle merely because they live under repo.
    return operation in {
        "cli audit start",
        "cli audit read",
        "cli audit summary",
        "cli audit status",
        "cli audit wait",
        "cli repo add",
        "cli repo list",
        "cli repo status",
        "cli recon start",
        "cli recon status",
        "cli portfolio status",
        "cli status",
        "cli wait",
        "cli schedule list",
        "cli schedule set",
        "cli schedule auto-time",
        "cli schedule timezone",
        "cli improvement-jobs list",
        "cli improvement-jobs set",
        "cli email list",
        "cli email set",
    }


def _with_catalog_changes(payload: object, changes: list[AuditCatalogChange]) -> object:
    rendered = [
        {
            "action_key": change.action_key,
            "changed_fields": list(change.changed_fields),
            "current": None,
            "kind": change.kind,
            "previous": None,
            "selector": change.action_key.removeprefix("audit."),
        }
        for change in changes
    ]
    audit_catalog = {"changes": rendered}
    if isinstance(payload, dict):
        return {**payload, "audit_catalog": audit_catalog}
    if isinstance(payload, (list, tuple)):
        return {"items": payload, "audit_catalog": audit_catalog}
    return {"value": payload, "audit_catalog": audit_catalog}


def _catalog_change_text(change: AuditCatalogChange) -> str:
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
    _run(lambda: _application(auth_file).auth.import_cookie(raw_cookie), _json_output(json_output))


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
    _run(lambda: _application(auth_file).auth.import_bearer_token(raw_token), _json_output(json_output))


@auth_app.command("status")
def auth_status(
    auth_file: Annotated[Path | None, typer.Option("--auth-file", hidden=True)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application(auth_file).auth.status(), _json_output(json_output))


@auth_app.command("refresh")
def auth_refresh(
    auth_file: Annotated[Path | None, typer.Option("--auth-file", hidden=True)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application(auth_file).auth.refresh(), _json_output(json_output))


@project_app.command("list")
def project_list(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().list_projects(), _json_output(json_output))


@project_app.command("create")
def project_create(name: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().create_project(name), _json_output(json_output))


@project_app.command("rename")
def project_rename(project: str, name: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().rename_project(project, name), _json_output(json_output))


@project_app.command("delete")
def project_delete(project: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().delete_project(project), _json_output(json_output))


@project_app.command("settings")
def project_settings(
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(
        lambda: _application().project_settings(_selected_project(project)),
        _json_output(json_output),
    )


@repo_app.command("list")
def repo_list(
    sort: Annotated[str, typer.Option("--sort")] = "default",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().portfolio_status(_repository_sort(sort)), _json_output(json_output))


@repo_app.command("resolve")
def repo_resolve(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().resolve_repository(repo, _selected_project(project)), _json_output(json_output))


@repo_app.command("add")
def repo_add(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().add_repository(repo, _selected_project(project)), _json_output(json_output))


@repo_app.command("remove")
def repo_remove(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().remove_repository(repo, _selected_project(project)), _json_output(json_output))


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
    )


@repo_app.command("status")
def repo_status(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().repository_status(repo, _selected_project(project)), _json_output(json_output))


@recon_app.command("start")
def recon_start(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().recon_start(repo, _selected_project(project)), _json_output(json_output))


@recon_app.command("status")
def recon_status(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().repository_status(repo, _selected_project(project)), _json_output(json_output))


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
    )


@audit_app.command("status")
def audit_status(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().repository_status(repo, _selected_project(project)), _json_output(json_output))


@audit_app.command("wait")
def audit_wait(
    repo: str,
    project: Annotated[str | None, typer.Option("--project")] = None,
    timeout: Annotated[str, typer.Option("--timeout")] = default_settings().audit_wait.timeout_text,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    settings = default_settings().audit_wait
    options = AuditWaitOptions(settings.poll_seconds, _parse_duration(timeout), settings.heartbeat_seconds)
    _run(
        lambda: _application().audit_wait(repo, project=_selected_project(project), options=options),
        _json_output(json_output),
    )


@portfolio_app.command("status")
def portfolio_status(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().portfolio_status(), _json_output(json_output))


@app.command("health")
def health(
    ready: Annotated[bool, typer.Option("--ready", help="Check MCP listener and cached backend readiness.")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not ready:
        _emit({"status": "ok"}, _json_output(json_output))
        return
    try:
        with socket.create_connection(
            (DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT), timeout=default_settings().service.local_readiness_timeout_seconds
        ):
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
    _run(lambda: _application().access(), _json_output(json_output))


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
    run_service(transport=transport, host=host, port=port, mount_path=mount_path)


@app.command("status")
def status(
    repo: str | None = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    sort: Annotated[str, typer.Option("--sort")] = "default",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    action = (
        (lambda: _application().repository_status(repo, _selected_project(project)))
        if repo is not None
        else (lambda: _application().portfolio_status(_repository_sort(sort)))
    )
    _run(action, _json_output(json_output))


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
    _run(lambda: _application().list_schedules(repo, _selected_project(project)), _json_output(json_output))


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
    update = AuditScheduleUpdate(enabled=_switch(enabled), cadence=frequency, timezone=timezone)
    _run(
        lambda: _application().set_schedules(repo, _selected_project(project), update, scope=scope),
        _json_output(json_output),
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
    update = AuditScheduleUpdate(timezone=timezone)
    _run(
        lambda: _application().set_schedules(repo, _selected_project(project), update, scope=scope),
        _json_output(json_output),
    )


@autofix_app.command("list")
def autofix_list(
    repo: str | None = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().list_autofixes(repo, _selected_project(project)), _json_output(json_output))


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
    update = AuditAutofixUpdate(_switch(enabled), frequency, timezone)
    _run(
        lambda: _application().set_autofixes(repo, _selected_project(project), selectors, update, scope=scope),
        _json_output(json_output),
    )


@email_app.command("list")
def email_list(
    repo: str | None = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().list_email_preferences(repo, _selected_project(project)), _json_output(json_output))


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
    )


@language_app.command("show")
def language_show(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    _run(lambda: _application().language(), _json_output(json_output))


@language_app.command("set")
def language_set(
    language: Annotated[Literal["en", "ru"], typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _run(lambda: _application().set_language(language), _json_output(json_output))


__all__ = ["app"]
