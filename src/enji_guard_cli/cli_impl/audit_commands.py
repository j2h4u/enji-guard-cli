from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import typer

from enji_guard_cli.cli_impl.rendering import echo_audit_start, echo_json

audit_app = typer.Typer(help="Start slow report-producing audits.")

type CommandRunner = Callable[..., object]
type CommandPathBuilder = Callable[..., str]
type JsonOutputResolver = Callable[[bool], bool]
type SelectedProjectResolver = Callable[[], str | None]
type RepoSelectorKindResolver = Callable[..., str]
type CommandPayloadResolver = Callable[[Callable[[], object]], object]
type StartReportAuditsAction = Callable[[str, str | None, list[str], bool], object]


@dataclass(frozen=True)
class AuditCommandsCliConfig:
    run_cli_journey: CommandRunner
    command_path: CommandPathBuilder
    json_output: JsonOutputResolver
    selected_project: SelectedProjectResolver
    selector_kind_for_repo: RepoSelectorKindResolver
    resolve_command_payload: CommandPayloadResolver
    start_report_audits: StartReportAuditsAction


_audit_commands_cli_config: AuditCommandsCliConfig | None = None


def configure_audit_commands(config: AuditCommandsCliConfig) -> None:
    global _audit_commands_cli_config
    _audit_commands_cli_config = config


@audit_app.command("start", help="Start one or more slow report-producing audits.")
def audit_start(
    repo: Annotated[str, typer.Argument(help="Repo id or owner/name.")],
    audits: Annotated[
        list[str] | None,
        typer.Argument(help="One or more report audit selectors. Use --all for all report audits."),
    ] = None,
    all_reports: Annotated[bool, typer.Option("--all", help="Start every report audit.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    project = _require_selected_project()()
    _require_runner()(
        lambda: _audit_start_body(
            repo=repo,
            project=project,
            audits=audits,
            all_reports=all_reports,
            json_output=json_output,
        ),
        command_path=_require_command_path()("audit", "start"),
        json_output=_require_json_output()(json_output),
        selector_kind=_require_selector_kind_for_repo()(repo, project=project, all_flag=all_reports),
        all_flag=all_reports,
    )


def _audit_start_body(
    *,
    repo: str,
    project: str | None,
    audits: list[str] | None,
    all_reports: bool,
    json_output: bool,
) -> object:
    payload = _require_resolve_command_payload()(
        lambda: _require_start_report_audits()(repo, project, audits or [], all_reports)
    )
    if _require_json_output()(json_output):
        echo_json(payload)
    else:
        echo_audit_start(payload)
    return payload


def _require_config() -> AuditCommandsCliConfig:
    if _audit_commands_cli_config is None:
        raise RuntimeError("audit commands are not configured")
    return _audit_commands_cli_config


def _require_runner() -> CommandRunner:
    return _require_config().run_cli_journey


def _require_command_path() -> CommandPathBuilder:
    return _require_config().command_path


def _require_json_output() -> JsonOutputResolver:
    return _require_config().json_output


def _require_selected_project() -> SelectedProjectResolver:
    return _require_config().selected_project


def _require_selector_kind_for_repo() -> RepoSelectorKindResolver:
    return _require_config().selector_kind_for_repo


def _require_resolve_command_payload() -> CommandPayloadResolver:
    return _require_config().resolve_command_payload


def _require_start_report_audits() -> StartReportAuditsAction:
    return _require_config().start_report_audits
