"""Subscription command registration for audit schedules, improvements, and email."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, TypeVar

import typer

from enji_guard_cli.application import (
    AUDIT_CADENCES,
    Application,
    EmailPreferencesWriteRequest,
    ImprovementJobWriteRequest,
    ScheduleWriteRequest,
    SubscriptionWriteScope,
)
from enji_guard_cli.delivery.cli.presentation import CliPresentation
from enji_guard_cli.delivery.cli.presenters import BATCH_MUTATION, EMAIL, IMPROVEMENT_JOBS, SCHEDULE

PayloadT = TypeVar("PayloadT")


class CommandRunner(Protocol):
    def __call__(
        self, action: Callable[[], PayloadT], as_json: bool, presentation: CliPresentation[PayloadT]
    ) -> None: ...


FREQUENCY_HELP = f"Run cadence: {', '.join(AUDIT_CADENCES)}."
TIMEZONE_HELP = "IANA timezone stored with each subscription, such as Asia/Almaty."
ENABLED_HELP = "Turn the subscription on or off."
AUTOMATIC_EXECUTION_HELP = "Turn automatic execution on or off for the improvement job."
DAYS_HELP = "Comma-separated run days: mon,tue,wed,thu,fri,sat,sun."
TIME_HELP = "Run time as HH:MM, or auto to use the automatic time source."
REPO_SCOPE_HELP = "Write to one repository; mutually exclusive with --all-repos and --all-projects."
REPO_FILTER_HELP = "Read one repository; omit to read every repository in scope."


@dataclass(frozen=True, slots=True)
class SubscriptionCommandApps:
    schedule_app: typer.Typer
    improvement_jobs_app: typer.Typer
    email_app: typer.Typer


@dataclass(frozen=True, slots=True)
class SubscriptionCommandDeps:
    application: Callable[[], Application]
    selected_project: Callable[[str | None], str | None]
    json_output: Callable[[bool], bool]
    run_command: CommandRunner
    scope: Callable[[bool, bool, str | None, bool, bool], SubscriptionWriteScope]
    switch: Callable[[Literal["on", "off"] | None], bool | None]


def register_subscription_commands(apps: SubscriptionCommandApps, deps: SubscriptionCommandDeps) -> None:
    """Attach recurring-audit subscription commands to their Typer groups."""

    @apps.schedule_app.command("list")
    def schedule_list(
        repo: Annotated[str | None, typer.Option("--repo", help=REPO_FILTER_HELP)] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        deps.run_command(
            lambda: deps.application().subscriptions.list_schedules(repo, deps.selected_project(project)),
            deps.json_output(json_output),
            SCHEDULE,
        )

    @apps.schedule_app.command("set")
    def schedule_set(  # noqa: PLR0913
        *,
        repo: Annotated[str | None, typer.Option("--repo", help=REPO_SCOPE_HELP)] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
        all_projects: Annotated[
            bool,
            typer.Option("--all-projects", help="Every repository in every project; requires --yes when not a TTY."),
        ] = False,
        yes: Annotated[bool, typer.Option("--yes", help="Confirm an --all-projects write without prompting.")] = False,
        enabled: Annotated[Literal["on", "off"] | None, typer.Option("--enabled", help=ENABLED_HELP)] = None,
        frequency: Annotated[str | None, typer.Option("--frequency", help=FREQUENCY_HELP)] = None,
        timezone: Annotated[str | None, typer.Option("--timezone", help=TIMEZONE_HELP)] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        as_json = deps.json_output(json_output)
        write_scope = deps.scope(all_repos, all_projects, repo, as_json, yes)
        deps.run_command(
            lambda: deps.application().subscriptions.set_schedules(
                ScheduleWriteRequest(
                    repo,
                    deps.selected_project(project),
                    enabled=deps.switch(enabled),
                    cadence=frequency,
                    timezone=timezone,
                    scope=write_scope,
                )
            ),
            as_json,
            BATCH_MUTATION,
        )

    @apps.schedule_app.command("auto-time")
    def schedule_auto_time(  # noqa: PLR0913
        *,
        repo: Annotated[str | None, typer.Option("--repo", help=REPO_SCOPE_HELP)] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
        all_projects: Annotated[
            bool,
            typer.Option("--all-projects", help="Every repository in every project; requires --yes when not a TTY."),
        ] = False,
        yes: Annotated[bool, typer.Option("--yes", help="Confirm an --all-projects write without prompting.")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        as_json = deps.json_output(json_output)
        write_scope = deps.scope(all_repos, all_projects, repo, as_json, yes)
        deps.run_command(
            lambda: deps.application().subscriptions.schedule_auto_time(
                repo, deps.selected_project(project), scope=write_scope
            ),
            as_json,
            BATCH_MUTATION,
        )

    @apps.schedule_app.command("timezone")
    def schedule_timezone(  # noqa: PLR0913
        *,
        timezone: Annotated[str, typer.Argument(help=TIMEZONE_HELP)],
        repo: Annotated[str | None, typer.Option("--repo", help=REPO_SCOPE_HELP)] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
        all_projects: Annotated[
            bool,
            typer.Option("--all-projects", help="Every repository in every project; requires --yes when not a TTY."),
        ] = False,
        yes: Annotated[bool, typer.Option("--yes", help="Confirm an --all-projects write without prompting.")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        as_json = deps.json_output(json_output)
        write_scope = deps.scope(all_repos, all_projects, repo, as_json, yes)
        deps.run_command(
            lambda: deps.application().subscriptions.set_schedules(
                ScheduleWriteRequest(repo, deps.selected_project(project), timezone=timezone, scope=write_scope)
            ),
            as_json,
            BATCH_MUTATION,
        )

    @apps.improvement_jobs_app.command("list")
    def improvement_job_list(
        repo: Annotated[str | None, typer.Option("--repo", help=REPO_FILTER_HELP)] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        deps.run_command(
            lambda: deps.application().subscriptions.list_improvement_jobs(repo, deps.selected_project(project)),
            deps.json_output(json_output),
            IMPROVEMENT_JOBS,
        )

    @apps.improvement_jobs_app.command("set")
    def improvement_job_set(  # noqa: PLR0913
        *,
        repo: Annotated[str | None, typer.Option("--repo", help=REPO_SCOPE_HELP)] = None,
        improvements: Annotated[list[str] | None, typer.Argument(help="Improvement selectors.")] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        all_improvements: Annotated[bool, typer.Option("--all", help="Every supported improvement selector.")] = False,
        all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
        all_projects: Annotated[
            bool,
            typer.Option("--all-projects", help="Every repository in every project; requires --yes when not a TTY."),
        ] = False,
        yes: Annotated[bool, typer.Option("--yes", help="Confirm an --all-projects write without prompting.")] = False,
        enabled: Annotated[Literal["on", "off"] | None, typer.Option("--enabled", help=ENABLED_HELP)] = None,
        automatic_execution: Annotated[
            Literal["on", "off"] | None, typer.Option("--automatic-execution", help=AUTOMATIC_EXECUTION_HELP)
        ] = None,
        frequency: Annotated[str | None, typer.Option("--frequency", help=FREQUENCY_HELP)] = None,
        days: Annotated[str | None, typer.Option("--days", help=DAYS_HELP)] = None,
        time: Annotated[str | None, typer.Option("--time", help=TIME_HELP)] = None,
        timezone: Annotated[str | None, typer.Option("--timezone", help=TIMEZONE_HELP)] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        selectors = ("__all__",) if all_improvements else tuple(improvements or ())
        as_json = deps.json_output(json_output)
        write_scope = deps.scope(all_repos, all_projects, repo, as_json, yes)
        deps.run_command(
            lambda: deps.application().subscriptions.set_improvement_jobs(
                ImprovementJobWriteRequest(
                    repo,
                    deps.selected_project(project),
                    selectors,
                    enabled=deps.switch(enabled),
                    automatic_execution=deps.switch(automatic_execution),
                    frequency=frequency,
                    days_of_week=_days(days),
                    schedule_time=time,
                    timezone=timezone,
                    scope=write_scope,
                )
            ),
            as_json,
            BATCH_MUTATION,
        )

    @apps.email_app.command("list")
    def email_list(
        repo: Annotated[str | None, typer.Option("--repo", help=REPO_FILTER_HELP)] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        deps.run_command(
            lambda: deps.application().subscriptions.list_email_preferences(repo, deps.selected_project(project)),
            deps.json_output(json_output),
            EMAIL,
        )

    @apps.email_app.command("set")
    def email_set(  # noqa: PLR0913
        *,
        repo: Annotated[str | None, typer.Option("--repo", help=REPO_SCOPE_HELP)] = None,
        project: Annotated[str | None, typer.Option("--project")] = None,
        all_repos: Annotated[bool, typer.Option("--all-repos")] = False,
        all_projects: Annotated[
            bool,
            typer.Option("--all-projects", help="Every repository in every project; requires --yes when not a TTY."),
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
        as_json = deps.json_output(json_output)
        write_scope = deps.scope(all_repos, all_projects, repo, as_json, yes)
        deps.run_command(
            lambda: deps.application().subscriptions.set_email_preferences(
                EmailPreferencesWriteRequest(
                    repo,
                    deps.selected_project(project),
                    manual=deps.switch(manual),
                    scheduled=deps.switch(scheduled),
                    scope=write_scope,
                )
            ),
            as_json,
            BATCH_MUTATION,
        )


def _days(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    return tuple(day.strip().lower() for day in value.split(",") if day.strip())
