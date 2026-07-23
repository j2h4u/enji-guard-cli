"""Concrete Enji Gateway adapter for Audit endpoint access."""

from typing import Literal, cast

from enji_guard_cli.audit.errors import AuditMalformedError, AuditNotFoundError, AuditUpstreamError
from enji_guard_cli.audit.ports import (
    AuditArtifact,
    AuditAutofixJob,
    AuditCatalogAction,
    AuditCatalogAutofix,
    AuditCatalogResult,
    AuditEmailPreference,
    AuditEmailPreferenceUpdate,
    AuditGatewayPort,
    AuditReportRef,
    AuditRerunState,
    AuditRun,
    AuditRunbookMetadata,
    AuditRunRequest,
    AuditRunResult,
    AuditRunsResult,
    AuditSchedule,
    AuditTaskBody,
    AuditTaskDetail,
    AuditTaskLink,
    AuditTaskLinksResult,
)
from enji_guard_cli.enji_gateway.http import (
    AuditRunCreate,
)
from enji_guard_cli.enji_gateway.http import (
    audit_auto_runs as _audit_auto_runs,
)
from enji_guard_cli.enji_gateway.http import (
    audit_email_preferences as _audit_email_preferences,
)
from enji_guard_cli.enji_gateway.http import (
    audit_reports as _audit_reports,
)
from enji_guard_cli.enji_gateway.http import (
    audit_summary_snapshot as _audit_summary_snapshot,
)
from enji_guard_cli.enji_gateway.http import (
    catalog as _catalog,
)
from enji_guard_cli.enji_gateway.http import (
    improvement_jobs as _improvement_jobs,
)
from enji_guard_cli.enji_gateway.http import (
    put_audit_auto_run as _put_audit_auto_run,
)
from enji_guard_cli.enji_gateway.http import (
    put_audit_email_preferences as _put_audit_email_preferences,
)
from enji_guard_cli.enji_gateway.http import (
    put_improvement_job as _put_improvement_job,
)
from enji_guard_cli.enji_gateway.http import (
    repo_active_runs as _repo_active_runs,
)
from enji_guard_cli.enji_gateway.http import (
    repo_audit_rerun_state as _repo_audit_rerun_state,
)
from enji_guard_cli.enji_gateway.http import (
    repo_task_links as _repo_task_links,
)
from enji_guard_cli.enji_gateway.http import (
    runbook as _runbook,
)
from enji_guard_cli.enji_gateway.http import (
    start_audit_run as _start_audit_run,
)
from enji_guard_cli.enji_gateway.http import (
    task_detail as _task_detail,
)
from enji_guard_cli.enji_gateway.ports import GatewayAuthFile, GatewayClient, GatewayCredentialReader
from enji_guard_cli.enji_gateway.wire import audit_artifact_from_snapshot, audit_report_refs_from_payload
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.json_types import JsonValue

HTTP_NOT_FOUND = 404


class AuditGateway(AuditGatewayPort):
    """Delegate Audit endpoint access to the existing Enji API adapter."""

    def __init__(
        self,
        auth_file: GatewayAuthFile = None,
        client: GatewayClient = None,
        *,
        auth_port: GatewayCredentialReader,
    ) -> None:
        self._auth_file = auth_file
        self._client = client
        self._auth_port = auth_port

    def catalog(self) -> AuditCatalogResult:
        payload = _catalog(self._auth_file, self._client, auth_port=self._auth_port)
        return AuditCatalogResult(
            actions=tuple(
                catalog_action
                for action in _object_list(payload.get("curatedActions"))
                if (catalog_action := _catalog_action(action)) is not None
            ),
            autofixes=tuple(
                autofix
                for action in _object_list(payload.get("auditAutofixes"))
                if (autofix := _catalog_autofix(action)) is not None
            ),
        )

    def active_runs(self, repo_id: str) -> AuditRunsResult:
        payload = _repo_active_runs(repo_id, self._auth_file, self._client, auth_port=self._auth_port)
        return AuditRunsResult(runs=tuple(_audit_run(run) for run in _object_list(payload.get("activeRuns"))))

    def rerun_state(self, repo_id: str) -> AuditRerunState:
        state = _object(
            _repo_audit_rerun_state(repo_id, self._auth_file, self._client, auth_port=self._auth_port).get("state")
        )
        return AuditRerunState(
            current_head_sha=_optional_str(state.get("currentHeadSha")),
            audited_head_sha=_optional_str(state.get("lastAuditedSha")),
            rerun_allowed=_optional_bool(state.get("canRerun")),
            last_task_id=_optional_str(state.get("lastFleetTaskId")),
            audited_head_shas={
                action_key: audited_sha
                for action_key, action in _object(state.get("actions")).items()
                if (audited_sha := _optional_str(_object(action).get("lastAuditedHeadSha"))) is not None
            },
        )

    def task_links(self, repo_id: str) -> AuditTaskLinksResult:
        payload = _repo_task_links(repo_id, self._auth_file, self._client, auth_port=self._auth_port)
        return AuditTaskLinksResult(
            links=tuple(
                AuditTaskLink(
                    task_id=_optional_str(link.get("fleetTaskId")),
                    action_key=_optional_str(link.get("actionKey")),
                    status=_optional_str(link.get("status")),
                    artifact_schema_name=_optional_str(link.get("artifactSchemaName")),
                    created_at=_optional_str(link.get("createdAt")),
                    started_at=_optional_str(link.get("startedAt")),
                    completed_at=_optional_str(link.get("completedAt")),
                )
                for link in _object_list(payload.get("links"))
            )
        )

    def task_detail(self, task_id: str) -> AuditTaskDetail:
        try:
            payload = _task_detail(task_id, self._auth_file, self._client, auth_port=self._auth_port)
        except EnjiApiError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                raise AuditNotFoundError(task_id) from exc
            if exc.response_malformed:
                raise AuditMalformedError(f"task detail payload for {task_id} is malformed") from exc
            raise AuditUpstreamError(f"task detail lookup failed for {task_id}: {exc.message}") from exc
        task = payload
        returned_task_id = _optional_str(task.get("id"))
        if returned_task_id is not None and returned_task_id != task_id:
            raise AuditMalformedError(f"task detail payload for {task_id} has mismatched task id")
        if returned_task_id is None or _optional_str(task.get("status")) is None:
            raise AuditMalformedError(f"task detail payload for {task_id} is malformed")
        return AuditTaskDetail(
            task_id=returned_task_id,
            status=_optional_str(task.get("status")),
            created_at=_optional_str(task.get("created_at")),
            started_at=_optional_str(task.get("started_at")),
            completed_at=_optional_str(task.get("completed_at")),
        )

    def runbook_metadata(self, runbook_id: str) -> AuditRunbookMetadata:
        payload = _runbook(runbook_id, self._auth_file, self._client, auth_port=self._auth_port)
        return AuditRunbookMetadata(
            runbook_id=runbook_id,
            title=_optional_str(payload.get("title")),
            description=_optional_str(payload.get("description")),
            suggested_flow=_optional_str(payload.get("suggested_flow")),
            suggested_flow_config=_object(payload.get("suggested_flow_config")),
        )

    def start_audit_run(self, request: AuditRunRequest) -> AuditRunResult:
        payload = _start_audit_run(
            AuditRunCreate(
                repo_id=request.repo_id,
                project_id=request.project_id,
                action_key=request.action_key,
                fleet_task_body=_fleet_task_body(request.action_key, request.task_body),
            ),
            self._auth_file,
            self._client,
            auth_port=self._auth_port,
        )
        task = _object(payload.get("task"))
        return AuditRunResult(
            task_id=_optional_str(task.get("id")),
            status=_optional_str(task.get("status")),
        )

    def list_audit_reports(self, repo_id: str, metric_group: str) -> tuple[AuditReportRef, ...]:
        payload = _audit_reports(repo_id, metric_group, self._auth_file, self._client, auth_port=self._auth_port)
        return audit_report_refs_from_payload(
            payload,
            expected_repo_id=repo_id,
            expected_metric_group=metric_group,
        )

    def read_audit_snapshot(
        self,
        repo_id: str,
        audit_key: str,
        metric_group: str | None = None,
        *,
        task_id: str,
    ) -> AuditArtifact:
        route_group = metric_group if isinstance(metric_group, str) and metric_group.strip() else audit_key
        artifact = audit_artifact_from_snapshot(
            _audit_summary_snapshot(
                repo_id,
                route_group,
                self._auth_file,
                self._client,
                task_id=task_id,
                auth_port=self._auth_port,
            ),
            audit_key,
        )
        return AuditArtifact(
            audit_key=artifact.audit_key,
            body=artifact.body,
            score=artifact.score,
            generated_at=artifact.generated_at,
            task_id=task_id,
            completed_at=artifact.completed_at,
            collected_at=artifact.collected_at,
        )

    def list_schedules(self, repo_id: str) -> tuple[AuditSchedule, ...]:
        payload = _audit_auto_runs(repo_id, self._auth_file, self._client, auth_port=self._auth_port)
        raw = payload.get("subscriptions")
        return tuple(schedule for item in _object_list(raw) if (schedule := _schedule(item)) is not None)

    def set_schedule(self, repo_id: str, audit_key: str, schedule: AuditSchedule) -> AuditSchedule:
        payload = _put_audit_auto_run(
            repo_id, audit_key, _schedule_payload(schedule), self._auth_file, self._client, auth_port=self._auth_port
        )
        raw = _object(payload.get("subscription")) or payload
        return _schedule(raw) or schedule

    def get_email_preferences(self, repo_id: str, audit_key: str) -> AuditEmailPreference:
        payload = _audit_email_preferences(repo_id, audit_key, self._auth_file, self._client, auth_port=self._auth_port)
        resolved = _object(payload.get("resolved"))
        return AuditEmailPreference(
            audit_key=audit_key,
            manual=_optional_bool(resolved.get("manualRunCompletion")),
            scheduled=_optional_bool(resolved.get("scheduledRunCompletion")),
        )

    def set_email_preference(
        self, repo_id: str, audit_key: str, update: AuditEmailPreferenceUpdate
    ) -> AuditEmailPreference:
        patch: dict[str, JsonValue] = {}
        if update.manual is not None:
            patch["manualRunCompletion"] = update.manual
        if update.scheduled is not None:
            patch["scheduledRunCompletion"] = update.scheduled
        if not patch:
            raise ValueError("pass --manual or --scheduled")
        payload = _put_audit_email_preferences(
            repo_id, audit_key, patch, self._auth_file, self._client, auth_port=self._auth_port
        )
        resolved = _object(payload.get("resolved"))
        return AuditEmailPreference(
            audit_key=audit_key,
            manual=_optional_bool(resolved.get("manualRunCompletion")),
            scheduled=_optional_bool(resolved.get("scheduledRunCompletion")),
        )

    def list_autofix_jobs(self, repo_id: str) -> tuple[AuditAutofixJob, ...]:
        payload = _improvement_jobs(repo_id, self._auth_file, self._client, auth_port=self._auth_port)
        return tuple(job for item in _object_list(payload.get("jobs")) if (job := _autofix_job(item)) is not None)

    def set_autofix_job(self, repo_id: str, kind: str, job: AuditAutofixJob) -> AuditAutofixJob:
        payload = _put_improvement_job(
            repo_id, kind, _autofix_job_payload(job), self._auth_file, self._client, auth_port=self._auth_port
        )
        return _autofix_job(_object(payload.get("job")) or payload) or job


def _audit_run(payload: dict[str, JsonValue]) -> AuditRun:
    return AuditRun(
        task_id=_optional_str(payload.get("fleetTaskId")),
        action_key=_optional_str(payload.get("actionKey")),
        status=_optional_str(payload.get("status")),
        created_at=_optional_str(payload.get("createdAt")),
        started_at=_optional_str(payload.get("startedAt")),
        completed_at=_optional_str(payload.get("completedAt")),
        projection_source=_optional_str(payload.get("projectionSource")),
        projection_status_source=_optional_str(payload.get("projectionStatusSource")),
        expires_at=_optional_str(payload.get("expiresAt")),
        current_head_sha=_optional_str(payload.get("currentHeadSha")),
        last_audited_head_sha=_optional_str(payload.get("lastAuditedHeadSha")),
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
        runbook_id=_optional_str(payload.get("fleetRunbookId")),
        artifact_schema_name=_optional_str(payload.get("artifactSchemaName")),
        artifact_schema_version=_optional_str(payload.get("artifactSchemaVersion")),
        task_description_template=_audit_description_template(_optional_str(payload.get("taskDescriptionTemplate"))),
    )


def _catalog_autofix(payload: dict[str, JsonValue]) -> AuditCatalogAutofix | None:
    action_key = payload.get("actionKey")
    variant_key = payload.get("variantKey")
    if not isinstance(action_key, str) or not isinstance(variant_key, str):
        return None
    sort_order = payload.get("sortOrder")
    return AuditCatalogAutofix(
        action_key=action_key,
        variant_key=variant_key,
        title=_optional_str(payload.get("title")),
        description=_optional_str(payload.get("description")),
        runbook_id=_optional_str(payload.get("fleetRunbookId")),
        status=_optional_str(payload.get("status")),
        sort_order=sort_order if isinstance(sort_order, int) and not isinstance(sort_order, bool) else None,
    )


def _object(value: JsonValue | None) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _object_list(value: JsonValue | None) -> list[dict[str, JsonValue]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _optional_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _audit_description_template(template: str | None) -> str | None:
    if template is None:
        return None
    placeholders = {
        "recurringPrefix": "recurring_prefix",
        "repoFullName": "repository_locator",
        "repoUrl": "repository_url",
        "linkedSites": "linked_websites",
        "artifactSchemaName": "artifact_schema_name",
        "artifactSchemaVersion": "artifact_schema_version",
        "artifactContract": "artifact_contract",
        "constraintsSection": "constraints",
    }
    for external, domain in placeholders.items():
        template = template.replace(f"{{{{{external}}}}}", f"{{{{{domain}}}}}")
    return template


def _optional_bool(value: JsonValue | None) -> bool | None:
    return value if isinstance(value, bool) else None


def _autofix_job(payload: dict[str, JsonValue]) -> AuditAutofixJob | None:
    action_key = _optional_str(payload.get("actionKey")) or _optional_str(payload.get("kind"))
    variant_key = _optional_str(payload.get("variantKey")) or _optional_str(payload.get("autofixVariantKey"))
    if action_key is None or variant_key is None:
        return None
    days = payload.get("daysOfWeek")
    source = _optional_str(payload.get("scheduleTimeSource"))
    known = {
        "actionKey",
        "variantKey",
        "kind",
        "enabled",
        "autoFix",
        "autofixVariantKey",
        "frequency",
        "daysOfWeek",
        "scheduleTime",
        "scheduleTimeSource",
        "timezone",
        "pentestMode",
    }
    return AuditAutofixJob(
        action_key=action_key,
        variant_key=variant_key,
        kind=_optional_str(payload.get("kind")),
        enabled=_optional_bool(payload.get("enabled")),
        auto_fix=_optional_bool(payload.get("autoFix")),
        autofix_variant_key=_optional_str(payload.get("autofixVariantKey")),
        frequency=_optional_str(payload.get("frequency")),
        days_of_week=tuple(item for item in days if isinstance(item, str)) if isinstance(days, list) else (),
        schedule_time=_optional_str(payload.get("scheduleTime")),
        schedule_time_source=cast(Literal["auto", "user"] | None, source if source in {"auto", "user"} else None),
        timezone=_optional_str(payload.get("timezone")),
        pentest_mode=_optional_str(payload.get("pentestMode")),
        extensions=tuple((key, value) for key, value in payload.items() if key not in known),
    )


def _autofix_job_payload(job: AuditAutofixJob) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "actionKey": job.action_key,
        "variantKey": job.variant_key,
        "enabled": job.enabled,
        "autoFix": job.auto_fix,
        "autofixVariantKey": job.autofix_variant_key,
        "frequency": job.frequency,
        "daysOfWeek": list(job.days_of_week),
        "scheduleTime": job.schedule_time,
        "scheduleTimeSource": job.schedule_time_source,
        "timezone": job.timezone,
        "pentestMode": job.pentest_mode,
    }
    if job.kind is not None:
        payload["kind"] = job.kind
    payload.update(dict(job.extensions))
    return payload


def _schedule(payload: dict[str, JsonValue]) -> AuditSchedule | None:
    key = _optional_str(payload.get("actionKey"))
    if key is None:
        return None
    day_of_month = payload.get("scheduleDayOfMonth")
    source = _optional_str(payload.get("scheduleTimeSource"))
    window_days = payload.get("windowDays")
    return AuditSchedule(
        audit_key=key,
        enabled=_optional_bool(payload.get("enabled")) is True,
        cadence=_optional_str(payload.get("cadence")),
        schedule_day=_optional_str(payload.get("scheduleDay")),
        schedule_day_of_month=day_of_month
        if isinstance(day_of_month, int) and not isinstance(day_of_month, bool)
        else None,
        schedule_time=_optional_str(payload.get("scheduleTime")),
        schedule_time_source=cast(Literal["auto", "user"] | None, source if source in {"auto", "user"} else None),
        timezone=_optional_str(payload.get("timezone")),
        window_days=tuple(item for item in window_days if isinstance(item, str))
        if isinstance(window_days, list)
        else (),
        window_start_time=_optional_str(payload.get("windowStartTime")),
        window_end_time=_optional_str(payload.get("windowEndTime")),
        window_mode=_optional_str(payload.get("windowMode")),
    )


def _schedule_payload(schedule: AuditSchedule) -> dict[str, JsonValue]:
    return {
        "cadence": schedule.cadence,
        "enabled": schedule.enabled,
        "scheduleDay": schedule.schedule_day,
        "scheduleDayOfMonth": schedule.schedule_day_of_month,
        "scheduleTime": schedule.schedule_time,
        "scheduleTimeSource": schedule.schedule_time_source,
        "timezone": schedule.timezone,
        "windowDays": list(schedule.window_days),
        "windowStartTime": schedule.window_start_time,
        "windowEndTime": schedule.window_end_time,
        "windowMode": schedule.window_mode,
    }


def _fleet_task_body(action_key: str, task: AuditTaskBody) -> dict[str, JsonValue]:
    """Translate the neutral Audit task into the Enji/Fleet wire schema."""

    return {
        "title": task.title,
        "description": _wire_description(action_key, task.description),
        "project_id": task.project_id,
        "execution_flow": task.execution_flow,
        "flow_config": dict(task.flow_config),
        "runbook_id": task.runbook_id,
        "scope_type": "project",
        "scope_owner": task.scope_owner,
        "origin_type": "manual",
        "repo_access_contexts": [
            {
                "provider": task.repository_provider,
                "repo_full_name": task.repository_locator,
            }
        ],
    }


def _wire_description(action_key: str, description: str) -> str:
    """Resolve the external report schema token only at the wire boundary."""

    schema = "upfront.recon.report" if action_key == "audit.recon" else "upfront.audit.report"
    return description.replace("{{reportSchemaName}}", schema)
