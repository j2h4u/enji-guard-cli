from collections.abc import Callable
from typing import cast

from enji_guard_cli.application import ApplicationResult, ApplicationRunner, AuditFacade, PortfolioFacade
from enji_guard_cli.mcp_facade import McpQueryFacade


class _FacadeSpy:
    """Record which facade method the narrow MCP surface reaches for."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def execute(self, action: object) -> ApplicationResult:
        self.calls.append(("execute",))
        return ApplicationResult(cast("Callable[[], object]", action)())

    def portfolio_overview(self, project: str | None, sort: str) -> object:
        self.calls.append(("portfolio_overview", project, sort))
        return {"scenario": "portfolio"}

    def audit_read(self, repo: str, selectors: list[str], *, project: str | None, all_audits: bool) -> object:
        self.calls.append(("audit_read", repo, selectors, project, all_audits))
        return {"scenario": "audits"}

    def audit_summary(self, repo: str, *, project: str | None) -> object:
        self.calls.append(("audit_summary", repo, project))
        return {"scenario": "summary"}


def test_mcp_facade_exposes_only_curated_query_scenarios() -> None:
    application = _FacadeSpy()
    facade = McpQueryFacade(
        cast("ApplicationRunner", application),
        cast("PortfolioFacade", application),
        cast("AuditFacade", application),
    )

    overview = facade.portfolio_overview("project", "weakest")
    audits = facade.repository_audits("owner/repo", "project")
    selected = facade.repository_audits("owner/repo", "project", [" audit.security "])

    assert overview.payload == {"scenario": "portfolio"}
    assert audits.payload == {"scenario": "summary"}
    assert selected.payload == {"scenario": "audits"}
    assert application.calls == [
        ("execute",),
        ("portfolio_overview", "project", "weakest"),
        ("execute",),
        ("audit_summary", "owner/repo", "project"),
        ("execute",),
        ("audit_read", "owner/repo", ["security"], "project", False),
    ]
    assert {name for name in dir(facade) if not name.startswith("_")} == {
        "portfolio_overview",
        "repository_audits",
    }
