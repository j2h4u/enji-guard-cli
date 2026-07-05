import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypeGuard, cast

import typer

from enji_guard_cli.audits import AuditAlias
from enji_guard_cli.cli_impl.rendering import (
    echo_audit_catalog,
    echo_auth_status,
    echo_json,
    echo_key_values,
)
from enji_guard_cli.cli_impl.rendering_support import object_dict
from enji_guard_cli.core import (
    AuthError,
    AuthStatusPayload,
    OperationName,
    import_bearer_token,
    import_cookie,
    refresh_auth,
    resolve_operation_result,
    resolve_operation_spec,
)

catalog_app = typer.Typer(help="Local audit aliases and metadata.")
auth_app = typer.Typer(help="Credential bootstrap, refresh, and status.")

CATALOG_AUDITS_HELP = resolve_operation_spec(OperationName.CATALOG_AUDITS).summary
CATALOG_AUDIT_HELP = resolve_operation_spec(OperationName.CATALOG_AUDIT).summary
AUTH_STATUS_HELP = resolve_operation_spec(OperationName.AUTH_STATUS).summary

type CommandRunner = Callable[..., object]
type CommandPathBuilder = Callable[..., str]
type JsonOutputResolver = Callable[[bool], bool]
type ErrorEchoer = Callable[[str, str], None]
type ErrorExitCodeResolver = Callable[[str], int]
type AuthStatusAction = Callable[[Path | None], object]
type AuthRefreshAction = Callable[[Path | None], object]


@dataclass(frozen=True)
class SharedCliConfig:
    run_cli_journey: CommandRunner
    command_path: CommandPathBuilder
    json_output: JsonOutputResolver
    echo_error: ErrorEchoer
    exit_code_for_error: ErrorExitCodeResolver


_shared_cli_config: SharedCliConfig | None = None
_auth_status_action: AuthStatusAction | None = None
_auth_refresh_action: AuthRefreshAction = refresh_auth


def configure_auth_catalog_commands(config: SharedCliConfig) -> None:
    global _shared_cli_config
    _shared_cli_config = config


def set_auth_status_action(action: AuthStatusAction) -> None:
    global _auth_status_action
    _auth_status_action = action


def set_auth_refresh_action(action: AuthRefreshAction) -> None:
    global _auth_refresh_action
    _auth_refresh_action = action


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


@catalog_app.command("audits", help=CATALOG_AUDITS_HELP)
def catalog_audits(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _require_runner()(
        lambda: _catalog_audits_body(json_output=json_output),
        command_path=_require_command_path()("catalog", "audits"),
        json_output=_require_json_output()(json_output),
        selector_kind="unknown",
    )


def _catalog_audits_body(*, json_output: bool) -> object:
    payload = resolve_operation_result(resolve_operation_spec(OperationName.CATALOG_AUDITS).execute())
    if _require_json_output()(json_output):
        echo_json(payload)
    else:
        echo_audit_catalog(payload)
    return payload


@catalog_app.command("audit", help=CATALOG_AUDIT_HELP)
def catalog_audit(
    audit: Annotated[AuditAlias, typer.Argument(help="Canonical audit alias.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _require_runner()(
        lambda: _catalog_audit_body(audit=audit, json_output=json_output),
        command_path=_require_command_path()("catalog", "audit"),
        json_output=_require_json_output()(json_output),
        selector_kind="unknown",
    )


def _catalog_audit_body(*, audit: AuditAlias, json_output: bool) -> object:
    payload = resolve_operation_result(resolve_operation_spec(OperationName.CATALOG_AUDIT).execute(audit))
    if _require_json_output()(json_output):
        echo_json(payload)
    else:
        echo_key_values(object_dict(payload))
    return payload


@auth_app.command("import-cookie", help="Import a raw browser Cookie header from stdin.")
def auth_import_cookie(
    stdin: Annotated[bool, typer.Option("--stdin", help="Read a raw Cookie header from stdin.")] = False,
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ~/.config/enji-guard/auth.json."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _require_runner()(
        lambda: _auth_import_cookie_body(stdin=stdin, auth_file=auth_file, json_output=json_output),
        command_path=_require_command_path()("auth", "import-cookie"),
        json_output=_require_json_output()(json_output),
        selector_kind="unknown",
    )


def _auth_import_cookie_body(*, stdin: bool, auth_file: Path | None, json_output: bool) -> object:
    if not stdin:
        _require_echo_error()("VALIDATION", "use --stdin to avoid storing cookies in shell history")
        raise typer.Exit(1)

    raw_cookie = sys.stdin.read()
    try:
        payload = import_cookie(raw_cookie, auth_file)
        if _require_json_output()(json_output):
            echo_json(payload)
        else:
            echo_key_values(cast(dict[str, object], payload))
        return payload
    except ValueError as exc:
        _require_echo_error()("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    except OSError as exc:
        _require_echo_error()("STORAGE", str(exc))
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
    _require_runner()(
        lambda: _auth_import_token_body(stdin=stdin, auth_file=auth_file, json_output=json_output),
        command_path=_require_command_path()("auth", "import-token"),
        json_output=_require_json_output()(json_output),
        selector_kind="unknown",
    )


def _auth_import_token_body(*, stdin: bool, auth_file: Path | None, json_output: bool) -> object:
    if not stdin:
        _require_echo_error()("VALIDATION", "use --stdin to avoid storing tokens in shell history")
        raise typer.Exit(1)

    raw_token = sys.stdin.read()
    try:
        payload = import_bearer_token(raw_token, auth_file)
        if _require_json_output()(json_output):
            echo_json(payload)
        else:
            echo_key_values(cast(dict[str, object], payload))
        return payload
    except ValueError as exc:
        _require_echo_error()("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    except OSError as exc:
        _require_echo_error()("STORAGE", str(exc))
        raise typer.Exit(1) from None


@auth_app.command("status", help=AUTH_STATUS_HELP)
def auth_status_command(
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ~/.config/enji-guard/auth.json."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _require_runner()(
        lambda: _auth_status_body(auth_file=auth_file, json_output=json_output),
        command_path=_require_command_path()("auth", "status"),
        json_output=_require_json_output()(json_output),
        selector_kind="unknown",
    )


def _auth_status_body(*, auth_file: Path | None, json_output: bool) -> object:
    payload = cast_auth_status_payload(resolve_operation_result(_require_auth_status_action()(auth_file)))
    if _require_json_output()(json_output):
        echo_json(payload)
    else:
        echo_auth_status(payload)
    if not payload["authenticated"]:
        raise typer.Exit(3)
    return payload


@auth_app.command("refresh", help="Refresh cookie auth and persist rotated cookies.")
def auth_refresh_command(
    auth_file: Annotated[
        Path | None,
        typer.Option("--auth-file", help="Auth file path. Defaults to ~/.config/enji-guard/auth.json."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    _require_runner()(
        lambda: _auth_refresh_body(auth_file=auth_file, json_output=json_output),
        command_path=_require_command_path()("auth", "refresh"),
        json_output=_require_json_output()(json_output),
        selector_kind="unknown",
    )


def _auth_refresh_body(*, auth_file: Path | None, json_output: bool) -> object:
    try:
        payload = _auth_refresh_action(auth_file)
        if _require_json_output()(json_output):
            echo_json(payload)
        else:
            echo_key_values(cast(dict[str, object], payload))
        return payload
    except AuthError as exc:
        _require_echo_error()(exc.code, exc.message)
        raise typer.Exit(_require_exit_code_for_error()(exc.code)) from None


def _require_auth_status_action() -> AuthStatusAction:
    if _auth_status_action is None:
        raise RuntimeError("auth status action not configured")
    return _auth_status_action


def _require_runner() -> CommandRunner:
    if _shared_cli_config is None:
        raise RuntimeError("CLI journey runner not configured")
    return _shared_cli_config.run_cli_journey


def _require_command_path() -> CommandPathBuilder:
    if _shared_cli_config is None:
        raise RuntimeError("command path builder not configured")
    return _shared_cli_config.command_path


def _require_json_output() -> JsonOutputResolver:
    if _shared_cli_config is None:
        raise RuntimeError("json output resolver not configured")
    return _shared_cli_config.json_output


def _require_echo_error() -> ErrorEchoer:
    if _shared_cli_config is None:
        raise RuntimeError("error echoer not configured")
    return _shared_cli_config.echo_error


def _require_exit_code_for_error() -> ErrorExitCodeResolver:
    if _shared_cli_config is None:
        raise RuntimeError("error exit code resolver not configured")
    return _shared_cli_config.exit_code_for_error
