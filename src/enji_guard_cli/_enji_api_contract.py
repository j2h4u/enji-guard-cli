from dataclasses import dataclass
from typing import Literal, TypedDict

type HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


class ApiEndpointPayload(TypedDict):
    method: HttpMethod
    path_template: str
    operation_id: str
    operation: str
    request_body_ref: str | None


@dataclass(frozen=True, slots=True)
class EnjiEndpointSpec:
    method: HttpMethod
    path_template: str
    operation_id: str
    operation: str
    request_body_ref: str | None = None

    def catalog_entry(self) -> ApiEndpointPayload:
        return {
            "method": self.method,
            "path_template": self.path_template,
            "operation_id": self.operation_id,
            "operation": self.operation,
            "request_body_ref": self.request_body_ref,
        }


ACCESS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/me/access",
    operation_id="getUxMeAccess",
    operation="access",
)
REPORTS_LIST_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/projects",
    operation_id="listProjects",
    operation="reports list",
)
PROJECTS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/projects",
    operation_id="listProjects",
    operation="repo list",
)
PROJECT_DETAIL_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/projects/{projectId}",
    operation_id="getProject",
    operation="project detail",
)
FLEET_PROJECT_CREATE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/v1/projects",
    operation_id="createFleetProject",
    operation="project create",
    request_body_ref="#/components/requestBodies/FleetProjectCreate",
)
UX_PROJECT_CREATE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/ux/projects",
    operation_id="createUxProject",
    operation="project create",
    request_body_ref="#/components/requestBodies/UxProjectCreate",
)
PROJECT_RENAME_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="PATCH",
    path_template="/api/ux/projects/{projectId}",
    operation_id="patchProject",
    operation="project rename",
    request_body_ref="#/components/requestBodies/ProjectPatch",
)
UX_PROJECT_DELETE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="DELETE",
    path_template="/api/ux/projects/{projectId}",
    operation_id="deleteUxProject",
    operation="project delete",
)
FLEET_PROJECT_DELETE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="DELETE",
    path_template="/api/v1/projects/{projectId}",
    operation_id="deleteFleetProject",
    operation="project delete",
)
REPO_TRANSFER_PREFLIGHT_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/ux/projects/{sourceProjectId}/repos/{repoId}/transfer/preflight",
    operation_id="preflightRepoTransfer",
    operation="repo move preflight",
    request_body_ref="#/components/requestBodies/RepoTransferPreflight",
)
REPO_TRANSFER_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/ux/projects/{sourceProjectId}/repos/{repoId}/transfer",
    operation_id="transferRepo",
    operation="repo move",
    request_body_ref="#/components/requestBodies/RepoTransfer",
)
CATALOG_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/catalog",
    operation_id="getUxCatalog",
    operation="catalog",
)
RUNBOOK_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/v1/runbooks/{runbookId}",
    operation_id="getRunbook",
    operation="runbook",
)
PROJECT_REPOS_ADD_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/ux/projects/{projectId}/repos",
    operation_id="addProjectRepo",
    operation="repo add",
    request_body_ref="#/components/requestBodies/GitHubRepoConnect",
)
PROJECT_REPO_DELETE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="DELETE",
    path_template="/api/ux/projects/{projectId}/repos/{repoId}",
    operation_id="deleteProjectRepo",
    operation="repo remove",
)
PROJECT_REPO_CONNECTION_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="PUT",
    path_template="/api/ux/projects/{projectId}/repos/{repoId}/connection",
    operation_id="putProjectRepoConnection",
    operation="repo add",
    request_body_ref="#/components/requestBodies/RepoConnectionUpdate",
)
REPO_ACTIVE_RUNS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/active-runs",
    operation_id="listRepoActiveRuns",
    operation="repo active runs",
)
REPO_AUDIT_RERUN_STATE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/audit-rerun-state",
    operation_id="getRepoAuditRerunState",
    operation="repo rerun state",
)
REPO_TASK_LINKS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/task-links",
    operation_id="listRepoTaskLinks",
    operation="repo task links",
)
TASK_DETAIL_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/v1/tasks/{taskId}",
    operation_id="getTask",
    operation="task detail",
)
REPO_AUDIT_RUNS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/ux/repos/{repoId}/audit-runs",
    operation_id="createRepoAuditRun",
    operation="audit start",
    request_body_ref="#/components/requestBodies/AuditRunCreate",
)
REPO_AUDIT_SUMMARY_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/snapshots/upfront.audit.summary",
    operation_id="getRepoAuditSummarySnapshot",
    operation="report show",
)
AUDIT_EMAIL_PREFERENCES_GET_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/audits/{actionKey}/email-preferences",
    operation_id="getAuditEmailPreferences",
    operation="email list",
)
AUDIT_EMAIL_PREFERENCES_PUT_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="PUT",
    path_template="/api/ux/repos/{repoId}/audits/{actionKey}/email-preferences",
    operation_id="putAuditEmailPreferences",
    operation="email set",
    request_body_ref="#/components/requestBodies/EmailPreferencesPatch",
)
IMPROVEMENT_JOBS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/improvement-jobs/{repoId}",
    operation_id="listImprovementJobs",
    operation="schedule list",
)
IMPROVEMENT_JOB_PUT_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="PUT",
    path_template="/api/ux/improvement-jobs/{repoId}/{kind}",
    operation_id="putImprovementJob",
    operation="schedule set",
    request_body_ref="#/components/schemas/ImprovementJobUpdate",
)

IMPLEMENTED_ENJI_ENDPOINTS = (
    ACCESS_ENDPOINT_SPEC,
    REPORTS_LIST_ENDPOINT_SPEC,
    PROJECTS_ENDPOINT_SPEC,
    PROJECT_DETAIL_ENDPOINT_SPEC,
    FLEET_PROJECT_CREATE_ENDPOINT_SPEC,
    UX_PROJECT_CREATE_ENDPOINT_SPEC,
    PROJECT_RENAME_ENDPOINT_SPEC,
    UX_PROJECT_DELETE_ENDPOINT_SPEC,
    FLEET_PROJECT_DELETE_ENDPOINT_SPEC,
    REPO_TRANSFER_PREFLIGHT_ENDPOINT_SPEC,
    REPO_TRANSFER_ENDPOINT_SPEC,
    CATALOG_ENDPOINT_SPEC,
    RUNBOOK_ENDPOINT_SPEC,
    PROJECT_REPOS_ADD_ENDPOINT_SPEC,
    PROJECT_REPO_DELETE_ENDPOINT_SPEC,
    PROJECT_REPO_CONNECTION_ENDPOINT_SPEC,
    REPO_ACTIVE_RUNS_ENDPOINT_SPEC,
    REPO_AUDIT_RERUN_STATE_ENDPOINT_SPEC,
    REPO_TASK_LINKS_ENDPOINT_SPEC,
    TASK_DETAIL_ENDPOINT_SPEC,
    REPO_AUDIT_RUNS_ENDPOINT_SPEC,
    REPO_AUDIT_SUMMARY_ENDPOINT_SPEC,
    AUDIT_EMAIL_PREFERENCES_GET_ENDPOINT_SPEC,
    AUDIT_EMAIL_PREFERENCES_PUT_ENDPOINT_SPEC,
    IMPROVEMENT_JOBS_ENDPOINT_SPEC,
    IMPROVEMENT_JOB_PUT_ENDPOINT_SPEC,
)


def implemented_api_endpoints() -> list[ApiEndpointPayload]:
    return [endpoint.catalog_entry() for endpoint in IMPLEMENTED_ENJI_ENDPOINTS]
