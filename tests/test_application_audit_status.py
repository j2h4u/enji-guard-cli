from application_builder import ApplicationStubs
from enji_guard_cli.audit.ports import (
    AuditCatalogAction,
    AuditCatalogResult,
    AuditRerunState,
    AuditRun,
    AuditRunsResult,
    AuditTaskLinksResult,
)


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

    def task_detail(self, task_id: str) -> object:
        raise AssertionError(f"the ledger must not need upstream task detail: {task_id}")

    def task_links(self, repo_id: str) -> AuditTaskLinksResult:
        assert repo_id == "repo-1"
        return AuditTaskLinksResult(())


def test_application_exposes_only_active_runs_in_repository_status() -> None:
    app = ApplicationStubs(audit_gateway=_AuditGateway()).build()

    status = app.audit_recon().status("repo-1")

    assert [run.task_id for run in status.active_runs] == ["running"]
