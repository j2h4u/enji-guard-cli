from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import typer

from enji_guard_cli.audits import AuditAlias, ReportAuditAlias
from enji_guard_cli.cli_impl.rendering import echo_json
from enji_guard_cli.cli_impl.report_rendering import echo_report_summary, report_summary_payload, reports_markdown

report_app = typer.Typer(help="Read generated audit reports.")

type CommandRunner = Callable[..., object]
type CommandPathBuilder = Callable[..., str]
type JsonOutputResolver = Callable[[bool], bool]
type ErrorEchoer = Callable[[str, str], None]
type SelectedProjectResolver = Callable[[], str | None]
type RepoSelectorKindResolver = Callable[..., str]
type CommandPayloadResolver = Callable[[Callable[[], object]], object]
type ReadReportsAction = Callable[[str, str | None, list[AuditAlias], bool], object]


@dataclass(frozen=True)
class ReportCommandsCliConfig:
    run_cli_journey: CommandRunner
    command_path: CommandPathBuilder
    json_output: JsonOutputResolver
    echo_error: ErrorEchoer
    selected_project: SelectedProjectResolver
    selector_kind_for_repo: RepoSelectorKindResolver
    resolve_command_payload: CommandPayloadResolver
    read_reports_for_repo: ReadReportsAction


_report_commands_cli_config: ReportCommandsCliConfig | None = None


def configure_report_commands(config: ReportCommandsCliConfig) -> None:
    global _report_commands_cli_config
    _report_commands_cli_config = config


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
    project = _require_selected_project()()
    _require_runner()(
        lambda: _report_read_body(
            repo=repo, project=project, audits=audits, all_reports=all_reports, json_output=json_output
        ),
        command_path=_require_command_path()("report", "read"),
        json_output=_require_json_output()(json_output),
        selector_kind=_require_selector_kind_for_repo()(repo, project=project, all_flag=all_reports),
        all_flag=all_reports,
    )


def _report_read_body(
    *,
    repo: str,
    project: str | None,
    audits: list[ReportAuditAlias] | None,
    all_reports: bool,
    json_output: bool,
) -> object:
    payload = _require_resolve_command_payload()(
        lambda: _require_read_reports_for_repo()(repo, project, _report_audits(audits or []), all_reports)
    )
    if _require_json_output()(json_output):
        echo_json(payload)
        return payload
    try:
        typer.echo(reports_markdown(payload))
    except ValueError as exc:
        _require_echo_error()("VALIDATION", str(exc))
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
    project = _require_selected_project()()
    _require_runner()(
        lambda: _report_summary_body(
            repo=repo,
            project=project,
            audits=audits,
            all_reports=all_reports,
            json_output=json_output,
        ),
        command_path=_require_command_path()("report", "summary"),
        json_output=_require_json_output()(json_output),
        selector_kind=_require_selector_kind_for_repo()(repo, project=project, all_flag=all_reports),
        all_flag=all_reports,
    )


def _report_summary_body(
    *,
    repo: str,
    project: str | None,
    audits: list[ReportAuditAlias] | None,
    all_reports: bool,
    json_output: bool,
) -> object:
    payload = _require_resolve_command_payload()(
        lambda: _require_read_reports_for_repo()(repo, project, _report_audits(audits or []), all_reports)
    )
    if _require_json_output()(json_output):
        echo_json(report_summary_payload(payload))
    else:
        echo_report_summary(payload)
    return payload


def _report_audit(audit: ReportAuditAlias) -> AuditAlias:
    return AuditAlias(audit.value)


def _report_audits(audits: list[ReportAuditAlias]) -> list[AuditAlias]:
    return [_report_audit(audit) for audit in audits]


def _require_config() -> ReportCommandsCliConfig:
    if _report_commands_cli_config is None:
        raise RuntimeError("report commands are not configured")
    return _report_commands_cli_config


def _require_runner() -> CommandRunner:
    return _require_config().run_cli_journey


def _require_command_path() -> CommandPathBuilder:
    return _require_config().command_path


def _require_json_output() -> JsonOutputResolver:
    return _require_config().json_output


def _require_echo_error() -> ErrorEchoer:
    return _require_config().echo_error


def _require_selected_project() -> SelectedProjectResolver:
    return _require_config().selected_project


def _require_selector_kind_for_repo() -> RepoSelectorKindResolver:
    return _require_config().selector_kind_for_repo


def _require_resolve_command_payload() -> CommandPayloadResolver:
    return _require_config().resolve_command_payload


def _require_read_reports_for_repo() -> ReadReportsAction:
    return _require_config().read_reports_for_repo
