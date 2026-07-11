from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

import typer

from enji_guard_cli.cli_impl.rendering import (
    echo_email_preferences_table,
    echo_json,
    echo_schedule_settings_table,
)
from enji_guard_cli.cli_impl.write_targets import (
    EmailSetCliArgs,
    ScheduleSetCliArgs,
    parse_email_set_args,
    parse_schedule_set_args,
)
from enji_guard_cli.core import EmailPreferenceUpdate, ScheduleSettingsUpdate

schedule_app = typer.Typer(help="Manage scheduled report audits.")
email_app = typer.Typer(help="Manage report completion email preferences.")

SCHEDULE_SET_EPILOG = """
Targets: REPO, --project PROJECT --all-repos, or --all-projects.
Options: --enabled on|off, --frequency daily|workdays|weekly-3x|weekly-2x|weekly|monthly, --timezone TZ, --json.
"""
EMAIL_SET_EPILOG = """
Targets: REPO, --project PROJECT --all-repos, or --all-projects.
Options: --manual on|off, --scheduled on|off, --json.
"""

type CommandRunner = Callable[..., object]
type CommandPathBuilder = Callable[..., str]
type JsonOutputResolver = Callable[[bool], bool]
type ErrorEchoer = Callable[[str, str], None]
type SelectedProjectResolver = Callable[[], str | None]
type RepoSelectorKindResolver = Callable[..., str]
type CommandPayloadResolver = Callable[[Callable[[], object]], object]
type ScheduleListAction = Callable[[str | None, str | None], object]
type ScheduleSetAction = Callable[..., object]
type EmailListAction = Callable[[str | None, str | None], object]
type EmailSetAction = Callable[..., object]


@dataclass(frozen=True)
class WritePreferencesCliConfig:
    run_cli_journey: CommandRunner
    command_path: CommandPathBuilder
    json_output: JsonOutputResolver
    echo_error: ErrorEchoer
    selected_project: SelectedProjectResolver
    selector_kind_for_repo: RepoSelectorKindResolver
    resolve_command_payload: CommandPayloadResolver
    list_schedule_settings: ScheduleListAction
    set_schedule_settings: ScheduleSetAction
    list_email_preferences: EmailListAction
    set_email_preferences: EmailSetAction


_write_preferences_cli_config: WritePreferencesCliConfig | None = None


def configure_write_preferences_commands(config: WritePreferencesCliConfig) -> None:
    global _write_preferences_cli_config
    _write_preferences_cli_config = config


@schedule_app.command("list", help="List automatic report audit schedules.")
def schedule_list(
    repo: Annotated[
        str | None,
        typer.Argument(help="Optional repo id or owner/name. Defaults to every repo in scope."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    project = _require_selected_project()()
    _require_runner()(
        lambda: _schedule_list_body(repo=repo, project=project, json_output=json_output),
        command_path=_require_command_path()("schedule", "list"),
        json_output=_require_json_output()(json_output),
        selector_kind=_require_selector_kind_for_repo()(repo, project=project),
    )


def _schedule_list_body(*, repo: str | None, project: str | None, json_output: bool) -> object:
    payload = _require_resolve_command_payload()(lambda: _require_list_schedule_settings()(repo, project))
    if _require_json_output()(json_output):
        echo_json(payload)
    else:
        echo_schedule_settings_table(payload)
    return payload


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
        _require_echo_error()("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    project = _require_selected_project()()
    all_flag = args.all_repos or args.all_projects
    _require_runner()(
        lambda: _schedule_set_body(args, project=project),
        command_path=_require_command_path()("schedule", "set"),
        json_output=args.json_output,
        selector_kind=_require_selector_kind_for_repo()(args.repo, project=project, all_flag=all_flag),
        all_flag=all_flag,
    )


def _schedule_set_body(args: ScheduleSetCliArgs, *, project: str | None) -> object:
    payload = _require_resolve_command_payload()(
        lambda: _require_set_schedule_settings()(
            args.repo,
            project,
            ScheduleSettingsUpdate(
                enabled=_preference_switch(args.enabled),
                cadence=args.cadence,
                window_days=None,
                schedule_time=None,
                timezone=args.timezone,
            ),
            all_repos=args.all_repos,
            all_projects=args.all_projects,
        )
    )
    if _require_json_output()(args.json_output):
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
    project = _require_selected_project()()
    all_flag = all_repos or all_projects
    _require_runner()(
        lambda: _schedule_auto_time_body(
            repo=repo,
            project=project,
            all_repos=all_repos,
            all_projects=all_projects,
            json_output=json_output,
        ),
        command_path=_require_command_path()("schedule", "auto-time"),
        json_output=_require_json_output()(json_output),
        selector_kind=_require_selector_kind_for_repo()(repo, project=project, all_flag=all_flag),
        all_flag=all_flag,
    )


def _schedule_auto_time_body(
    *,
    repo: str | None,
    project: str | None,
    all_repos: bool,
    all_projects: bool,
    json_output: bool,
) -> object:
    payload = _require_resolve_command_payload()(
        lambda: _require_set_schedule_settings()(
            repo,
            project,
            ScheduleSettingsUpdate(
                enabled=None,
                cadence=None,
                window_days=None,
                schedule_time="auto",
                timezone=None,
            ),
            all_repos=all_repos,
            all_projects=all_projects,
        )
    )
    if _require_json_output()(json_output):
        echo_json(payload)
    else:
        echo_schedule_settings_table(payload)
    return payload


@email_app.command("list", help="List manual and scheduled report email preferences.")
def email_list(
    repo: Annotated[
        str | None,
        typer.Argument(help="Optional repo id or owner/name. Defaults to every repo in scope."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    project = _require_selected_project()()
    _require_runner()(
        lambda: _email_list_body(repo=repo, project=project, json_output=json_output),
        command_path=_require_command_path()("email", "list"),
        json_output=_require_json_output()(json_output),
        selector_kind=_require_selector_kind_for_repo()(repo, project=project),
    )


def _email_list_body(*, repo: str | None, project: str | None, json_output: bool) -> object:
    payload = _require_resolve_command_payload()(lambda: _require_list_email_preferences()(repo, project))
    if _require_json_output()(json_output):
        echo_json(payload)
    else:
        echo_email_preferences_table(payload)
    return payload


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
        _require_echo_error()("VALIDATION", str(exc))
        raise typer.Exit(1) from None
    project = _require_selected_project()()
    all_flag = args.all_repos or args.all_projects
    _require_runner()(
        lambda: _email_set_body(args, project=project),
        command_path=_require_command_path()("email", "set"),
        json_output=args.json_output,
        selector_kind=_require_selector_kind_for_repo()(args.repo, project=project, all_flag=all_flag),
        all_flag=all_flag,
    )


def _email_set_body(args: EmailSetCliArgs, *, project: str | None) -> object:
    payload = _require_resolve_command_payload()(
        lambda: _require_set_email_preferences()(
            args.repo,
            project,
            EmailPreferenceUpdate(
                manual_run_completion=_preference_switch(args.manual),
                scheduled_run_completion=_preference_switch(args.scheduled),
            ),
            all_repos=args.all_repos,
            all_projects=args.all_projects,
        )
    )
    if _require_json_output()(args.json_output):
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


def _require_runner() -> CommandRunner:
    if _write_preferences_cli_config is None:
        raise RuntimeError("CLI journey runner not configured")
    return _write_preferences_cli_config.run_cli_journey


def _require_command_path() -> CommandPathBuilder:
    if _write_preferences_cli_config is None:
        raise RuntimeError("command path builder not configured")
    return _write_preferences_cli_config.command_path


def _require_json_output() -> JsonOutputResolver:
    if _write_preferences_cli_config is None:
        raise RuntimeError("json output resolver not configured")
    return _write_preferences_cli_config.json_output


def _require_echo_error() -> ErrorEchoer:
    if _write_preferences_cli_config is None:
        raise RuntimeError("error echoer not configured")
    return _write_preferences_cli_config.echo_error


def _require_selected_project() -> SelectedProjectResolver:
    if _write_preferences_cli_config is None:
        raise RuntimeError("selected project resolver not configured")
    return _write_preferences_cli_config.selected_project


def _require_selector_kind_for_repo() -> RepoSelectorKindResolver:
    if _write_preferences_cli_config is None:
        raise RuntimeError("repo selector kind resolver not configured")
    return _write_preferences_cli_config.selector_kind_for_repo


def _require_resolve_command_payload() -> CommandPayloadResolver:
    if _write_preferences_cli_config is None:
        raise RuntimeError("command payload resolver not configured")
    return _write_preferences_cli_config.resolve_command_payload


def _require_list_schedule_settings() -> ScheduleListAction:
    if _write_preferences_cli_config is None:
        raise RuntimeError("schedule list action not configured")
    return _write_preferences_cli_config.list_schedule_settings


def _require_set_schedule_settings() -> ScheduleSetAction:
    if _write_preferences_cli_config is None:
        raise RuntimeError("schedule set action not configured")
    return _write_preferences_cli_config.set_schedule_settings


def _require_list_email_preferences() -> EmailListAction:
    if _write_preferences_cli_config is None:
        raise RuntimeError("email list action not configured")
    return _write_preferences_cli_config.list_email_preferences


def _require_set_email_preferences() -> EmailSetAction:
    if _write_preferences_cli_config is None:
        raise RuntimeError("email set action not configured")
    return _write_preferences_cli_config.set_email_preferences
