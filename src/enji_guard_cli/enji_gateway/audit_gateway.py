"""Concrete Enji Gateway adapter for Audit endpoint access."""

from enji_guard_cli.enji_api import (
    AuditRunCreate,
)
from enji_guard_cli.enji_api import (
    audit_summary_snapshot as _audit_summary_snapshot,
)
from enji_guard_cli.enji_api import (
    catalog as _catalog,
)
from enji_guard_cli.enji_api import (
    repo_active_runs as _repo_active_runs,
)
from enji_guard_cli.enji_api import (
    repo_audit_rerun_state as _repo_audit_rerun_state,
)
from enji_guard_cli.enji_api import (
    repo_task_links as _repo_task_links,
)
from enji_guard_cli.enji_api import (
    runbook as _runbook,
)
from enji_guard_cli.enji_api import (
    start_audit_run as _start_audit_run,
)
from enji_guard_cli.enji_api import (
    task_detail as _task_detail,
)
from enji_guard_cli.enji_gateway.ports import (
    AuditArtifact,
    AuditCatalogAction,
    AuditCatalogResult,
    AuditGatewayPort,
    AuditRerunState,
    AuditRun,
    AuditRunbookMetadata,
    AuditRunRequest,
    AuditRunResult,
    AuditRunsResult,
    AuditTaskDetail,
    AuditTaskLink,
    AuditTaskLinksResult,
    GatewayAuthFile,
    GatewayClient,
)
from enji_guard_cli.enji_gateway.wire import audit_artifact_from_snapshot
from enji_guard_cli.json_types import JsonValue


class AuditGateway(AuditGatewayPort):
    """Delegate Audit endpoint access to the existing Enji API adapter."""

    def __init__(self, auth_file: GatewayAuthFile = None, client: GatewayClient = None) -> None:
        self._auth_file = auth_file
        self._client = client

    def catalog(self) -> AuditCatalogResult:
        payload = _catalog(self._auth_file, self._client)
        return AuditCatalogResult(
            actions=tuple(
                catalog_action
                for action in _object_list(payload.get("curatedActions"))
                if (catalog_action := _catalog_action(action)) is not None
            )
        )

    def active_runs(self, repo_id: str) -> AuditRunsResult:
        payload = _repo_active_runs(repo_id, self._auth_file, self._client)
        return AuditRunsResult(runs=tuple(_audit_run(run) for run in _object_list(payload.get("activeRuns"))))

    def rerun_state(self, repo_id: str) -> AuditRerunState:
        state = _object(_repo_audit_rerun_state(repo_id, self._auth_file, self._client).get("state"))
        return AuditRerunState(
            current_head_sha=_optional_str(state.get("currentHeadSha")),
            audited_head_sha=_optional_str(state.get("lastAuditedSha")),
            rerun_allowed=_optional_bool(state.get("canRerun")),
            last_task_id=_optional_str(state.get("lastFleetTaskId")),
        )

    def task_links(self, repo_id: str) -> AuditTaskLinksResult:
        payload = _repo_task_links(repo_id, self._auth_file, self._client)
        return AuditTaskLinksResult(
            links=tuple(
                AuditTaskLink(
                    task_id=_optional_str(link.get("fleetTaskId")),
                    action_key=_optional_str(link.get("actionKey")),
                    status=_optional_str(link.get("status")),
                )
                for link in _object_list(payload.get("links"))
            )
        )

    def task_detail(self, task_id: str) -> AuditTaskDetail:
        task = _object(_task_detail(task_id, self._auth_file, self._client).get("task"))
        return AuditTaskDetail(
            task_id=_optional_str(task.get("id")) or task_id, status=_optional_str(task.get("status"))
        )

    def runbook_metadata(self, runbook_id: str) -> AuditRunbookMetadata:
        payload = _runbook(runbook_id, self._auth_file, self._client)
        return AuditRunbookMetadata(
            runbook_id=runbook_id,
            title=_optional_str(payload.get("title")),
            description=_optional_str(payload.get("description")),
        )

    def start_audit_run(self, request: AuditRunRequest) -> AuditRunResult:
        payload = _start_audit_run(
            AuditRunCreate(
                repo_id=request.repo_id,
                project_id=request.project_id,
                action_key=request.action_key,
                fleet_task_body=request.task_body,
            ),
            self._auth_file,
            self._client,
        )
        task = _object(payload.get("task"))
        return AuditRunResult(task_id=_optional_str(task.get("id")), status=_optional_str(task.get("status")))

    def read_audit_snapshot(self, repo_id: str, audit_key: str) -> AuditArtifact:
        return audit_artifact_from_snapshot(
            _audit_summary_snapshot(repo_id, audit_key, self._auth_file, self._client),
            audit_key,
        )


def _audit_run(payload: dict[str, JsonValue]) -> AuditRun:
    return AuditRun(
        task_id=_optional_str(payload.get("fleetTaskId")),
        action_key=_optional_str(payload.get("actionKey")),
        status=_optional_str(payload.get("status")),
        created_at=_optional_str(payload.get("createdAt")),
        started_at=_optional_str(payload.get("startedAt")),
        completed_at=_optional_str(payload.get("completedAt")),
    )


def _catalog_action(payload: dict[str, JsonValue]) -> AuditCatalogAction | None:
    action_key = payload.get("actionKey")
    title = payload.get("title")
    if not isinstance(action_key, str) or not isinstance(title, str):
        return None
    return AuditCatalogAction(
        action_key=action_key,
        title=title,
        category=_optional_str(payload.get("category")),
        status=_optional_str(payload.get("status")),
        metric_group=_optional_str(payload.get("metricGroup")),
        runbook_kind=_optional_str(payload.get("runbookKind")),
    )


def _object(value: JsonValue | None) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _object_list(value: JsonValue | None) -> list[dict[str, JsonValue]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _optional_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: JsonValue | None) -> bool | None:
    return value if isinstance(value, bool) else None
