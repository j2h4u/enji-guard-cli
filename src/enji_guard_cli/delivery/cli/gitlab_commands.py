"""GitLab discovery command registration for the Typer CLI."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Protocol, TypeVar

import typer

from enji_guard_cli.application import Application, GitLabProjectsRequest
from enji_guard_cli.delivery.cli.presentation import CliPresentation
from enji_guard_cli.delivery.cli.presenters import GITLAB_CREDENTIALS, GITLAB_PROJECTS

PayloadT = TypeVar("PayloadT")


class CommandRunner(Protocol):
    def __call__(
        self, action: Callable[[], PayloadT], as_json: bool, presentation: CliPresentation[PayloadT]
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class GitLabCommandDeps:
    application: Callable[[], Application]
    json_output: Callable[[bool], bool]
    run_command: CommandRunner


def register_gitlab_commands(gitlab_app: typer.Typer, deps: GitLabCommandDeps) -> None:
    """Attach GitLab discovery commands to the provided Typer group."""

    @gitlab_app.command("credentials")
    def gitlab_credentials(
        scope_type: Annotated[str | None, typer.Option("--scope-type")] = None,
        scope_owner: Annotated[str | None, typer.Option("--scope-owner")] = None,
        limit: Annotated[int, typer.Option("--limit", min=1)] = 50,
        offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        deps.run_command(
            lambda: deps.application().gitlab.gitlab_credentials(
                scope_type=scope_type,
                scope_owner=scope_owner,
                limit=limit,
                offset=offset,
            ),
            deps.json_output(json_output),
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
        deps.run_command(
            lambda: deps.application().gitlab.gitlab_projects(
                GitLabProjectsRequest(
                    credential_id=credential_id,
                    search=search,
                    page=page,
                    per_page=per_page,
                    all_pages=all_pages,
                    scope_type=scope_type,
                    scope_owner=scope_owner,
                )
            ),
            deps.json_output(json_output),
            GITLAB_PROJECTS,
        )
