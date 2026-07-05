from enji_guard_cli.core import RepoSort, RepoStatusAllPayload, read_reports_for_repo, runtime_status

__all__ = [
    "RepoSort",
    "RepoStatusAllPayload",
    "read_repository_reports",
    "repository_portfolio_overview",
]


def repository_portfolio_overview(
    project: str | None,
    sort: RepoSort,
) -> RepoStatusAllPayload:
    return runtime_status(None, project, sort)


def read_repository_reports(repo: str, project: str | None) -> dict[str, object]:
    return read_reports_for_repo(repo, project, [], all_reports=True)
