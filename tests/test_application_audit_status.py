from typing import cast

from enji_guard_cli.application import Application
from enji_guard_cli.audit.ports import (
    AuditCatalogAction,
    AuditCatalogResult,
    AuditGatewayPort,
    AuditRerunState,
    AuditRun,
    AuditRunsResult,
    AuditTaskLinksResult,
)
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.portfolio.ports import PortfolioGatewayPort


class _AuditGateway:
    def catalog(self) -> AuditCatalogResult:
        return AuditCatalogResult(
            actions=(
                AuditCatalogAction(
                    "audit.recon",
                    "Recon",
                    "workflow",
                    "draft",
                    None,
                    "recon",
                ),
                AuditCatalogAction(
                    "audit.security",
                    "Security",
                    "audit",
                    "published",
                    "vulns",
                    "audit",
                    artifact_schema_name="upfront.audit.summary",
                    artifact_schema_version="v1",
                ),
            )
        )

    def active_runs(self, repo_id: str) -> AuditRunsResult:
        assert repo_id == "repo-1"
        return AuditRunsResult(
            (
                AuditRun("running", "audit.security", "in_progress", None, None, None),
                AuditRun("completed", "audit.security", "completed", None, None, "2026-01-01T00:00:00Z"),
            )
        )

    def rerun_state(self, repo_id: str) -> AuditRerunState:
        assert repo_id == "repo-1"
        return AuditRerunState("head", None, None, None, {"audit.security": "old"})

    def task_links(self, repo_id: str) -> AuditTaskLinksResult:
        assert repo_id == "repo-1"
        return AuditTaskLinksResult(())


def test_application_exposes_only_active_runs_in_repository_status() -> None:
    app = Application(
        cast(AuditGatewayPort, _AuditGateway()),
        cast(PortfolioGatewayPort, object()),
        cast(AuthSessionService, object()),
    )

    _status, active_runs = app._audit_status_with_runs("repo-1")

    assert [run.task_id for run in active_runs] == ["running"]
