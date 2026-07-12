from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

import typer

from enji_guard_cli.cli_impl.rendering import echo_autofix_settings_table, echo_json
from enji_guard_cli.cli_impl.write_targets import AutofixSetCliArgs, parse_autofix_set_args
from enji_guard_cli.core import AutofixSettingsUpdate, AutofixWriteScope

autofix_app = typer.Typer(help="Manage curated Enji autofix jobs.")

AUTOFIX_SET_EPILOG = """
Targets: REPO, --project PROJECT --all-repos, or --all-projects.
Options: --all, --enabled on|off, --frequency daily|workdays|weekly-3x|weekly-2x|weekly|monthly, --timezone TZ, --json.
"""

type CommandRunner = Callable[..., object]
type CommandPathBuilder = Callable[..., str]
type JsonOutputResolver = Callable[[bool], bool]
type ErrorEchoer = Callable[[str, str], None]
type SelectedProjectResolver = Callable[[], str | None]
type RepoSelectorKindResolver = Callable[..., str]
type CommandPayloadResolver = Callable[[Callable[[], object]], object]
type AutofixListAction = Callable[[str | None, str | None], object]
type AutofixSetAction = Callable[..., object]


@dataclass(frozen=True)
class AutofixCliConfig:
    run_cli_journey: CommandRunner
    command_path: CommandPathBuilder
    json_output: JsonOutputResolver
    echo_error: ErrorEchoer
    selected_project: SelectedProjectResolver
    selector_kind_for_repo: RepoSelectorKindResolver
    resolve_command_payload: CommandPayloadResolver
    list_autofix_settings: AutofixListAction
    set_autofix_settings: AutofixSetAction


_autofix_cli_config: AutofixCliConfig | None = None


def configure_autofix_commands(config: AutofixCliConfig) -> None:
    global _autofix_cli_config
    _autofix_cli_config = config


@autofix_app.command("list", help="List published autofix jobs.")
def autofix_list(
    repo: Annotated[
        str | None, typer.Argument(help="Optional repo id or owner/name. Defaults to every repo in scope.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    project = _require_config().selected_project()
    config = _require_config()
    config.run_cli_journey(
        lambda: _autofix_list_body(repo, project, json_output),
        command_path=config.command_path("autofix", "list"),
        json_output=config.json_output(json_output),
        selector_kind=config.selector_kind_for_repo(repo, project=project),
    )


def _autofix_list_body(repo: str | None, project: str | None, json_output: bool) -> object:
    config = _require_config()
    payload = config.resolve_command_payload(lambda: config.list_autofix_settings(repo, project))
    if config.json_output(json_output):
        echo_json(payload)
    else:
        echo_autofix_settings_table(payload)
    return payload


@autofix_app.command(
    "set",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Update selected curated autofix jobs.",
    epilog=AUTOFIX_SET_EPILOG,
    options_metavar="[OPTIONS] [REPO] [AUTOFIXES]...",
)
def autofix_set(ctx: typer.Context) -> None:
    try:
        args = parse_autofix_set_args(ctx.args)
    except ValueError as exc:
        _require_config().echo_error("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    project = _require_config().selected_project()
    all_flag = args.all_repos or args.all_projects
    config = _require_config()
    config.run_cli_journey(
        lambda: _autofix_set_body(args, project),
        command_path=config.command_path("autofix", "set"),
        json_output=args.json_output,
        selector_kind=config.selector_kind_for_repo(args.repo, project=project, all_flag=all_flag),
        all_flag=all_flag,
    )


def _autofix_set_body(args: AutofixSetCliArgs, project: str | None) -> object:
    config = _require_config()
    selectors = ["__all__"] if args.all_autofixes else args.selectors
    payload = config.resolve_command_payload(
        lambda: config.set_autofix_settings(
            args.repo,
            project,
            selectors,
            AutofixSettingsUpdate(_preference_switch(args.enabled), args.frequency, args.timezone),
            scope=AutofixWriteScope(all_repos=args.all_repos, all_projects=args.all_projects),
        )
    )
    if config.json_output(args.json_output):
        echo_json(payload)
    else:
        echo_autofix_settings_table(payload)
    return payload


def _preference_switch(value: Literal["on", "off"] | None) -> bool | None:
    return True if value == "on" else False if value == "off" else None


def _require_config() -> AutofixCliConfig:
    if _autofix_cli_config is None:
        raise RuntimeError("autofix CLI commands not configured")
    return _autofix_cli_config
