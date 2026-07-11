from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from enji_guard_cli.audits import AuditCatalog
from enji_guard_cli.core_impl.models import (
    ProjectRuntimeStatusPayload,
    ReportStatusPayload,
    RepoRuntimeStatusPayload,
    RepoStatusAllPayload,
    RepoStatusSummaryPayload,
    RepoTargetPayload,
)
from enji_guard_cli.core_impl.payloads import json_dict, json_object_list, json_str
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

type ProjectDetail = Callable[[str], JsonObjectPayload]
type RepoRuntimeStatus = Callable[[str, str | None, dict[str, JsonValue]], RepoRuntimeStatusPayload]
type MatchingRepoTargets = Callable[[str, list[str]], list[RepoTargetPayload]]
type SelectedProjectIds = Callable[[str | None], list[str]]
type RepoRuntimeStatusFromTarget = Callable[[RepoTargetPayload], RepoRuntimeStatusPayload]
type RepoTarget = Callable[[str, str | None, dict[str, JsonValue]], RepoTargetPayload]
type EmptyReportStatus = Callable[[str, AuditCatalog], ReportStatusPayload]
type RepoActiveRuns = Callable[[str], list[JsonValue]]
type GetRepoRerunState = Callable[[str], JsonObjectPayload | None]
type ListRepoTaskLinks = Callable[[str], JsonObjectPayload]
type CurrentHeadSha = Callable[[JsonObjectPayload | None], str | None]
type ReportStatusFromTaskLinks = Callable[
    [str, JsonObjectPayload, list[JsonValue], JsonObjectPayload | None, AuditCatalog],
    ReportStatusPayload,
]


@dataclass(frozen=True, slots=True)
class RuntimeStatusDependencies:
    repo_active_runs: RepoActiveRuns
    get_repo_rerun_state: GetRepoRerunState
    list_repo_task_links: ListRepoTaskLinks
    current_head_sha: CurrentHeadSha
    report_status_from_task_links: ReportStatusFromTaskLinks
    catalog: AuditCatalog


@dataclass(frozen=True, slots=True)
class RepoInventoryStatusContext:
    project_id: str
    project_name: str | None
    repo: dict[str, JsonValue]
    catalog: AuditCatalog


def project_runtime_status(
    project_id: str,
    *,
    project_detail: ProjectDetail,
    repo_runtime_status: RepoRuntimeStatus,
) -> ProjectRuntimeStatusPayload:
    project = project_detail(project_id)
    project_payload = json_dict(project.get("project"))
    project_name = json_str(project_payload.get("name"))
    return {
        "project_id": project_id,
        "project_name": project_name,
        "repos": [
            repo_runtime_status(project_id, project_name, repo)
            for repo in json_object_list(project.get("repos"))
            if json_str(repo.get("id")) is not None
        ],
    }


def project_inventory_status(
    project_id: str,
    *,
    project_detail: ProjectDetail,
    repo_inventory_status: RepoRuntimeStatus,
) -> ProjectRuntimeStatusPayload:
    project = project_detail(project_id)
    project_payload = json_dict(project.get("project"))
    project_name = json_str(project_payload.get("name"))
    return {
        "project_id": project_id,
        "project_name": project_name,
        "repos": [
            repo_inventory_status(project_id, project_name, repo)
            for repo in json_object_list(project.get("repos"))
            if json_str(repo.get("id")) is not None
        ],
    }


def project_statuses_for_repo(
    repo: str,
    project: str | None,
    *,
    matching_repo_targets: MatchingRepoTargets,
    selected_project_ids: SelectedProjectIds,
    repo_runtime_status_from_target: RepoRuntimeStatusFromTarget,
) -> list[ProjectRuntimeStatusPayload]:
    grouped: dict[str, ProjectRuntimeStatusPayload] = {}
    for target in matching_repo_targets(repo, selected_project_ids(project)):
        project_id = target["project_id"]
        if project_id not in grouped:
            grouped[project_id] = {
                "project_id": project_id,
                "project_name": target["project_name"],
                "repos": [],
            }
        grouped[project_id]["repos"].append(repo_runtime_status_from_target(target))
    return list(grouped.values())


def repo_runtime_status(
    project_id: str,
    project_name: str | None,
    repo: dict[str, JsonValue],
    *,
    repo_target: RepoTarget,
    repo_runtime_status_from_target: RepoRuntimeStatusFromTarget,
) -> RepoRuntimeStatusPayload:
    return repo_runtime_status_from_target(repo_target(project_id, project_name, repo))


def repo_runtime_status_from_target(
    target: RepoTargetPayload,
    *,
    dependencies: RuntimeStatusDependencies,
) -> RepoRuntimeStatusPayload:
    repo_id = target["repo_id"]
    active_runs = dependencies.repo_active_runs(repo_id)
    rerun_state = dependencies.get_repo_rerun_state(repo_id)
    reports = dependencies.report_status_from_task_links(
        repo_id,
        dependencies.list_repo_task_links(repo_id),
        active_runs,
        rerun_state,
        dependencies.catalog,
    )
    payload: RepoRuntimeStatusPayload = {
        "project_id": target["project_id"],
        "project_name": target["project_name"],
        "repo_id": repo_id,
        "github_owner": target["github_owner"],
        "github_name": target["github_name"],
        "github_repo": target["github_repo"],
        "connected": target["connected"],
        "recon_done": target["recon_done"],
        "scores": target["scores"],
        "score_grades": target["score_grades"],
        "score_summary": target["score_summary"],
        "active_run_count": len(active_runs),
        "active_runs": active_runs,
        "current_head_sha": dependencies.current_head_sha(rerun_state),
        "last_report_at": reports["last_report_at"],
        "reports": reports,
    }
    return payload


def repo_inventory_status(
    context: RepoInventoryStatusContext,
    *,
    repo_target: RepoTarget,
    empty_report_status: EmptyReportStatus,
) -> RepoRuntimeStatusPayload:
    target = repo_target(context.project_id, context.project_name, context.repo)
    reports = empty_report_status(target["repo_id"], context.catalog)
    payload: RepoRuntimeStatusPayload = {
        "project_id": target["project_id"],
        "project_name": target["project_name"],
        "repo_id": target["repo_id"],
        "github_owner": target["github_owner"],
        "github_name": target["github_name"],
        "github_repo": target["github_repo"],
        "connected": target["connected"],
        "recon_done": target["recon_done"],
        "scores": target["scores"],
        "score_grades": target["score_grades"],
        "score_summary": target["score_summary"],
        "active_run_count": 0,
        "active_runs": [],
        "current_head_sha": None,
        "last_report_at": None,
        "reports": reports,
    }
    return payload


def repo_status_all_payload(projects: list[ProjectRuntimeStatusPayload]) -> RepoStatusAllPayload:
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "summary": repo_status_summary(projects),
        "projects": projects,
    }


def repo_status_summary(projects: list[ProjectRuntimeStatusPayload]) -> RepoStatusSummaryPayload:
    repos = [repo for project in projects for repo in project["repos"]]
    return {
        "project_count": len(projects),
        "repo_count": len(repos),
        "connected_repo_count": sum(1 for repo in repos if repo["connected"] is True),
        "active_run_count": sum(repo["active_run_count"] for repo in repos),
        "recon_done_count": sum(1 for repo in repos if repo["recon_done"] is True),
        "report_complete_count": sum(1 for repo in repos if repo["reports"]["complete"]),
    }
