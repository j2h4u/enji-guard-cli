from dataclasses import dataclass
from typing import Literal, TypedDict

from enji_guard_cli.transport_types import RetryProfile

type HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


class ApiEndpointPayload(TypedDict):
    method: HttpMethod
    path_template: str
    operation_id: str
    operation: str
    retry_profile: RetryProfile
    request_body_ref: str | None


@dataclass(frozen=True, slots=True)
class EnjiEndpointSpec:
    method: HttpMethod
    path_template: str
    operation_id: str
    operation: str
    retry_profile: RetryProfile
    request_body_ref: str | None = None

    def catalog_entry(self) -> ApiEndpointPayload:
        return {
            "method": self.method,
            "path_template": self.path_template,
            "operation_id": self.operation_id,
            "operation": self.operation,
            "retry_profile": self.retry_profile,
            "request_body_ref": self.request_body_ref,
        }


ACCESS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/me/access",
    operation_id="getUxMeAccess",
    operation="access",
    retry_profile=RetryProfile.READ,
)
GIT_CREDENTIALS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/v1/credentials",
    operation_id="listGitCredentials",
    operation="gitlab credentials",
    retry_profile=RetryProfile.READ,
)
GITLAB_PROJECTS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/v1/gitlab/projects",
    operation_id="listGitLabProjects",
    operation="gitlab projects",
    retry_profile=RetryProfile.READ,
)
USER_PREFERENCES_GET_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/user-preferences",
    operation_id="getUserPreferences",
    operation="language show",
    retry_profile=RetryProfile.READ,
)
USER_PREFERENCES_PUT_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="PUT",
    path_template="/api/ux/user-preferences",
    operation_id="putUserPreferences",
    operation="language set",
    retry_profile=RetryProfile.IDEMPOTENT_MUTATION,
    request_body_ref="#/components/requestBodies/UserLanguageUpdate",
)
REPORTS_LIST_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/projects",
    operation_id="listProjects",
    operation="reports list",
    retry_profile=RetryProfile.READ,
)
PROJECTS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/projects",
    operation_id="listProjects",
    operation="repo list",
    retry_profile=RetryProfile.READ,
)
PROJECT_DETAIL_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/projects/{projectId}",
    operation_id="getProject",
    operation="project detail",
    retry_profile=RetryProfile.READ,
)
PROJECT_ACTIVE_RUNS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/projects/{projectId}/active-runs",
    operation_id="listProjectActiveRuns",
    operation="project active runs",
    retry_profile=RetryProfile.READ,
)
PROJECT_RUN_LANGUAGE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/projects/{projectId}/run-language",
    operation_id="getProjectRunLanguage",
    operation="language show",
    retry_profile=RetryProfile.READ,
)
FLEET_PROJECT_CREATE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/v1/projects",
    operation_id="createFleetProject",
    operation="project create",
    retry_profile=RetryProfile.UNSAFE_MUTATION,
    request_body_ref="#/components/requestBodies/FleetProjectCreate",
)
UX_PROJECT_CREATE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/ux/projects",
    operation_id="createUxProject",
    operation="project create",
    retry_profile=RetryProfile.UNSAFE_MUTATION,
    request_body_ref="#/components/requestBodies/UxProjectCreate",
)
PROJECT_RENAME_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="PATCH",
    path_template="/api/ux/projects/{projectId}",
    operation_id="patchProject",
    operation="project rename",
    retry_profile=RetryProfile.IDEMPOTENT_MUTATION,
    request_body_ref="#/components/requestBodies/ProjectPatch",
)
UX_PROJECT_DELETE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="DELETE",
    path_template="/api/ux/projects/{projectId}",
    operation_id="deleteUxProject",
    operation="project delete",
    retry_profile=RetryProfile.IDEMPOTENT_MUTATION,
)
FLEET_PROJECT_DELETE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="DELETE",
    path_template="/api/v1/projects/{projectId}",
    operation_id="deleteFleetProject",
    operation="project delete",
    retry_profile=RetryProfile.IDEMPOTENT_MUTATION,
)
REPO_TRANSFER_PREFLIGHT_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/ux/projects/{sourceProjectId}/repos/{repoId}/transfer/preflight",
    operation_id="preflightRepoTransfer",
    operation="repo move preflight",
    retry_profile=RetryProfile.SAFE_PROBE,
    request_body_ref="#/components/requestBodies/RepoTransferPreflight",
)
REPO_TRANSFER_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/ux/projects/{sourceProjectId}/repos/{repoId}/transfer",
    operation_id="transferRepo",
    operation="repo move",
    retry_profile=RetryProfile.UNSAFE_MUTATION,
    request_body_ref="#/components/requestBodies/RepoTransfer",
)
CATALOG_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/catalog",
    operation_id="getUxCatalog",
    operation="catalog",
    retry_profile=RetryProfile.READ,
)
RUNBOOK_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/v1/runbooks/{runbookId}",
    operation_id="getRunbook",
    operation="runbook",
    retry_profile=RetryProfile.READ,
)
PROJECT_REPOS_ADD_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/ux/projects/{projectId}/repos",
    operation_id="addProjectRepo",
    operation="repo add",
    retry_profile=RetryProfile.UNSAFE_MUTATION,
    request_body_ref="#/components/requestBodies/RepositoryConnect",
)
PROJECT_REPO_DELETE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="DELETE",
    path_template="/api/ux/projects/{projectId}/repos/{repoId}",
    operation_id="deleteProjectRepo",
    operation="repo remove",
    retry_profile=RetryProfile.IDEMPOTENT_MUTATION,
)
PROJECT_REPO_CONNECTION_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="PUT",
    path_template="/api/ux/projects/{projectId}/repos/{repoId}/connection",
    operation_id="putProjectRepoConnection",
    operation="repo add",
    retry_profile=RetryProfile.IDEMPOTENT_MUTATION,
    request_body_ref="#/components/requestBodies/RepoConnectionUpdate",
)
REPO_ACTIVE_RUNS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/active-runs",
    operation_id="listRepoActiveRuns",
    operation="repo active runs",
    retry_profile=RetryProfile.READ,
)
REPO_AUDIT_RERUN_STATE_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/audit-rerun-state",
    operation_id="getRepoAuditRerunState",
    operation="repo rerun state",
    retry_profile=RetryProfile.READ,
)
REPO_TASK_LINKS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/task-links",
    operation_id="listRepoTaskLinks",
    operation="repo task links",
    retry_profile=RetryProfile.READ,
)
TASK_DETAIL_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/v1/tasks/{taskId}",
    operation_id="getTask",
    operation="task detail",
    retry_profile=RetryProfile.READ,
)
REPO_AUDIT_RUNS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="POST",
    path_template="/api/ux/repos/{repoId}/audit-runs",
    operation_id="createRepoAuditRun",
    operation="audit start",
    retry_profile=RetryProfile.UNSAFE_MUTATION,
    request_body_ref="#/components/requestBodies/AuditRunCreate",
)
REPO_AUDIT_SUMMARY_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/snapshots/upfront.audit.summary",
    operation_id="getRepoAuditSummarySnapshot",
    operation="report show",
    retry_profile=RetryProfile.READ,
)
AUDIT_EMAIL_PREFERENCES_GET_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/audits/{actionKey}/email-preferences",
    operation_id="getAuditEmailPreferences",
    operation="email list",
    retry_profile=RetryProfile.READ,
)
AUDIT_EMAIL_PREFERENCES_PUT_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="PUT",
    path_template="/api/ux/repos/{repoId}/audits/{actionKey}/email-preferences",
    operation_id="putAuditEmailPreferences",
    operation="email set",
    retry_profile=RetryProfile.IDEMPOTENT_MUTATION,
    request_body_ref="#/components/requestBodies/EmailPreferencesPatch",
)
AUDIT_AUTO_RUNS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/repos/{repoId}/audit-auto-runs",
    operation_id="listRepoAuditAutoRuns",
    operation="schedule list",
    retry_profile=RetryProfile.READ,
)
AUDIT_AUTO_RUN_PUT_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="PUT",
    path_template="/api/ux/repos/{repoId}/audit-auto-runs/{actionKey}",
    operation_id="putRepoAuditAutoRun",
    operation="schedule set",
    retry_profile=RetryProfile.IDEMPOTENT_MUTATION,
    request_body_ref="#/components/requestBodies/AuditAutoRunSubscriptionUpdate",
)
IMPROVEMENT_JOBS_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="GET",
    path_template="/api/ux/improvement-jobs/{repoId}",
    operation_id="listImprovementJobs",
    operation="autofix list",
    retry_profile=RetryProfile.READ,
)
IMPROVEMENT_JOB_PUT_ENDPOINT_SPEC = EnjiEndpointSpec(
    method="PUT",
    path_template="/api/ux/improvement-jobs/{repoId}/{kind}",
    operation_id="putImprovementJob",
    operation="autofix set",
    retry_profile=RetryProfile.IDEMPOTENT_MUTATION,
    request_body_ref="#/components/schemas/ImprovementJobUpdate",
)

IMPLEMENTED_ENJI_ENDPOINTS = (
    ACCESS_ENDPOINT_SPEC,
    GIT_CREDENTIALS_ENDPOINT_SPEC,
    GITLAB_PROJECTS_ENDPOINT_SPEC,
    REPORTS_LIST_ENDPOINT_SPEC,
    PROJECTS_ENDPOINT_SPEC,
    PROJECT_DETAIL_ENDPOINT_SPEC,
    PROJECT_ACTIVE_RUNS_ENDPOINT_SPEC,
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
    AUDIT_AUTO_RUNS_ENDPOINT_SPEC,
    AUDIT_AUTO_RUN_PUT_ENDPOINT_SPEC,
    IMPROVEMENT_JOBS_ENDPOINT_SPEC,
    IMPROVEMENT_JOB_PUT_ENDPOINT_SPEC,
)


def implemented_api_endpoints() -> list[ApiEndpointPayload]:
    return [endpoint.catalog_entry() for endpoint in IMPLEMENTED_ENJI_ENDPOINTS]
