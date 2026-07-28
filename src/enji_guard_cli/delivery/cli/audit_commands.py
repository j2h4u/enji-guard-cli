"""Audit workflow command registration for the Typer CLI."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Protocol, TypeVar

import typer

from enji_guard_cli.application import Application
from enji_guard_cli.delivery.cli.presentation import CliPresentation
from enji_guard_cli.delivery.cli.presenters import AUDIT_READ, AUDIT_SUMMARY, AUDIT_WAIT, OPERATION
from enji_guard_cli.settings import default_settings

PayloadT = TypeVar("PayloadT")


class CommandRunner(Protocol):
    def __call__(
        self, action: Callable[[], PayloadT], as_json: bool, presentation: CliPresentation[PayloadT]
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AuditCommandApps:
    root_app: typer.Typer
    audit_app: typer.Typer


@dataclass(frozen=True, slots=True)
class AuditCommandDeps:
    application: Callable[[], Application]
    selected_project: Callable[[str | None], str | None]
    json_output: Callable[[bool], bool]
    run_command: CommandRunner
    fail: Callable[[str, str, bool], typer.Exit]
    parse_duration: Callable[[str], int]


def register_audit_commands(apps: AuditCommandApps, deps: AuditCommandDeps) -> None:
    """Attach repository audit workflow commands to the CLI."""

    def audit_selectors(audits: list[str] | None) -> list[str]:
        return [item.removeprefix("audit.") for item in (audits or [])]

    def explicit_audit_selectors(audits: list[str] | None, *, all_audits: bool, as_json: bool) -> list[str]:
        """Reject a selector list combined with --all instead of letting --all win."""
        selectors = audit_selectors(audits)
        if all_audits and selectors:
            raise deps.fail("VALIDATION", "pass audit selectors or --all, not both", as_json)
        return selectors

    @apps.audit_app.command("start")
    def audit_start(
        repo: str,
        audits: Annotated[list[str] | None, typer.Argument(help="Audit selector suffixes.")] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        all_audits: Annotated[bool, typer.Option("--all", help="Start every published audit.")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        as_json = deps.json_output(json_output)
        selectors = explicit_audit_selectors(audits, all_audits=all_audits, as_json=as_json)
        deps.run_command(
            lambda: deps.application().audit.audit_start(
                repo,
                deps.selected_project(project),
                selectors,
                all_audits=all_audits,
            ),
            as_json,
            OPERATION,
        )

    @apps.audit_app.command("read")
    def audit_read(
        repo: str,
        audits: Annotated[list[str] | None, typer.Argument(help="Audit selector suffixes.")] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        all_audits: Annotated[bool, typer.Option("--all", help="Read every published audit.")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        as_json = deps.json_output(json_output)
        selectors = explicit_audit_selectors(audits, all_audits=all_audits, as_json=as_json)
        deps.run_command(
            lambda: deps.application().audit.audit_read(
                repo,
                selectors,
                project=deps.selected_project(project),
                all_audits=all_audits,
            ),
            as_json,
            AUDIT_READ,
        )

    @apps.audit_app.command("summary")
    def audit_summary(
        repo: str,
        audits: Annotated[
            list[str] | None,
            typer.Argument(help="Optional audit selector suffixes; omit to summarize every published audit."),
        ] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        deps.run_command(
            lambda: deps.application().audit.audit_summary(
                repo, audit_selectors(audits), project=deps.selected_project(project)
            ),
            deps.json_output(json_output),
            AUDIT_SUMMARY,
        )

    @apps.root_app.command("wait", help="Block until repository audits finish. Do not use short timeouts as refresh.")
    def wait(
        repo: str,
        project: Annotated[str | None, typer.Option("--project")] = None,
        timeout: Annotated[str, typer.Option("--timeout")] = default_settings().audit_wait.timeout_text,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        deps.run_command(
            lambda: deps.application().audit.audit_wait(
                repo, project=deps.selected_project(project), timeout_seconds=deps.parse_duration(timeout)
            ),
            deps.json_output(json_output),
            AUDIT_WAIT,
        )
