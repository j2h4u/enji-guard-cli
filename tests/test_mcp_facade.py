from collections.abc import Callable
from typing import cast

from enji_guard_cli.application import Application, ApplicationResult
from enji_guard_cli.mcp_facade import McpQueryFacade


class _ApplicationSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def execute(self, action: object) -> ApplicationResult:
        self.calls.append(("execute",))
        return ApplicationResult(cast("Callable[[], object]", action)())

    def portfolio_overview(self, project: str | None, sort: str) -> object:
        self.calls.append(("portfolio_overview", project, sort))
        return {"scenario": "portfolio"}

    def audit_read(self, repo: str, *, project: str | None, all_audits: bool) -> object:
        self.calls.append(("audit_read", repo, project, all_audits))
        return {"scenario": "audits"}


def test_mcp_facade_exposes_only_curated_query_scenarios() -> None:
    application = _ApplicationSpy()
    facade = McpQueryFacade(cast("Application", application))

    overview = facade.portfolio_overview("project", "weakest")
    audits = facade.repository_audits("owner/repo", "project")

    assert overview.payload == {"scenario": "portfolio"}
    assert audits.payload == {"scenario": "audits"}
    assert application.calls == [
        ("execute",),
        ("portfolio_overview", "project", "weakest"),
        ("execute",),
        ("audit_read", "owner/repo", "project", True),
    ]
    assert {name for name in dir(facade) if not name.startswith("_")} == {
        "portfolio_overview",
        "repository_audits",
    }
