import time
from datetime import UTC, datetime

from enji_guard_cli.audit import AuditCatalog, AuditDefinition
from enji_guard_cli.core_impl.models import (
    FAILED_REPORT_WAIT_STATUSES,
    REPORT_ARTIFACT_SCHEMA,
    SCORE_EXCELLENT_THRESHOLD,
    SCORE_FAIR_THRESHOLD,
    SCORE_GOOD_THRESHOLD,
    SCORE_POOR_THRESHOLD,
    TERMINAL_RUN_STATUSES,
    ProjectRuntimeStatusPayload,
    ReportAuditStatusPayload,
    ReportFreshnessState,
    ReportStatusPayload,
    ReportTaskLifecycleState,
    ReportWaitOptions,
    ReportWaitPayload,
    ReportWaitReason,
    RepoRuntimeStatusPayload,
    RepoSort,
    ScoreGrade,
    ScoreSummaryPayload,
)
from enji_guard_cli.core_impl.payloads import json_dict, json_list, json_object_list, json_str
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue


def report_status_from_task_links(
    repo_id: str,
    payload: JsonObjectPayload,
    active_runs: list[JsonValue],
    rerun_state: JsonObjectPayload | None,
    catalog: AuditCatalog,
) -> ReportStatusPayload:
    links_by_action = _report_links_by_action(payload)
    active_runs_by_action = active_runs_by_action_map(active_runs)
    current_sha = current_head_sha(rerun_state)
    reports = [
        _report_audit_status(audit, links_by_action, active_runs_by_action, current_sha, rerun_state)
        for audit in catalog.published_audits
    ]
    latest_report_at = last_report_at(reports)
    readable = _audits_with_readable_reports(reports)
    active = _audits_with_active_tasks(reports)
    queued = _audits_with_task_lifecycle(reports, "queued")
    running = _audits_with_task_lifecycle(reports, "running")
    missing = _audits_with_unreadable_reports(reports)
    stale = _stale_report_audits(reports)
    failed = _failed_report_audits(reports)
    return {
        "schema_version": 2,
        "repo_id": repo_id,
        "current_head_sha": current_sha,
        "last_report_at": latest_report_at,
        "complete": not active and not missing,
        "fresh": not stale,
        "readable": bool(readable),
        "active": bool(active),
        "queued": bool(queued),
        "running": bool(running),
        "missing": bool(missing),
        "stale": bool(stale),
        "failed": bool(failed),
        "counts": {
            "total": len(reports),
            "readable": len(readable),
            "active": len(active),
            "queued": len(queued),
            "running": len(running),
            "missing": len(missing),
            "stale": len(stale),
            "failed": len(failed),
        },
        "items": reports,
    }


def _report_links_by_action(payload: JsonObjectPayload) -> dict[str, dict[str, JsonValue]]:
    links_by_action: dict[str, dict[str, JsonValue]] = {}
    for link in json_object_list(payload.get("links")):
        action_key = json_str(link.get("actionKey"))
        artifact_schema = json_str(link.get("artifactSchemaName"))
        if action_key is not None and artifact_schema == REPORT_ARTIFACT_SCHEMA:
            links_by_action[action_key] = link
    return links_by_action


def _report_audit_status(
    audit: AuditDefinition,
    links_by_action: dict[str, dict[str, JsonValue]],
    active_runs_by_action: dict[str, dict[str, JsonValue]],
    current_sha: str | None,
    rerun_state: JsonObjectPayload | None,
) -> ReportAuditStatusPayload:
    action_key = audit.action_key
    link = links_by_action.get(action_key)
    active_run = active_runs_by_action.get(action_key)
    audited_head_sha = last_audited_head_sha(rerun_state, action_key)
    stale = out_of_date(current_sha, audited_head_sha)
    task_lifecycle_state = _task_lifecycle_state(active_run)
    return {
        "audit": audit.action_key,
        "label": audit.title,
        "action_key": action_key,
        "metric_group": audit.metric_group,
        "report": {
            "readability_state": "readable" if link is not None else "unavailable",
            "can_read": link is not None,
            "freshness_state": _freshness_state(link, stale),
            "current_head_sha": current_sha,
            "audited_head_sha": audited_head_sha,
            "created_at": _link_value(link, "createdAt"),
            "started_at": _link_value(link, "startedAt"),
            "completed_at": _link_value(link, "completedAt"),
            "run_status": _link_value(link, "status"),
            "fleet_task_id": _link_value(link, "fleetTaskId"),
            "stale": stale,
        },
        "task": {
            "lifecycle_state": task_lifecycle_state,
            "active": task_lifecycle_state in {"queued", "running"},
            "fleet_task_id": _active_run_value(active_run, "fleetTaskId"),
            "run_status": _active_run_value(active_run, "status"),
            "created_at": _active_run_value(active_run, "createdAt"),
            "started_at": _active_run_value(active_run, "startedAt"),
            "completed_at": _active_run_value(active_run, "completedAt"),
        },
        "agent_action": action_key,
    }


def current_head_sha(rerun_state: JsonObjectPayload | None) -> str | None:
    if rerun_state is None:
        return None
    state = json_dict(rerun_state.get("state"))
    return json_str(state.get("currentHeadSha"))


def last_audited_head_sha(rerun_state: JsonObjectPayload | None, action_key: str) -> str | None:
    if rerun_state is None:
        return None
    state = json_dict(rerun_state.get("state"))
    actions = json_dict(state.get("actions"))
    action = json_dict(actions.get(action_key))
    return json_str(action.get("lastAuditedHeadSha"))


def out_of_date(current_sha: str | None, last_audited_sha: str | None) -> bool | None:
    if current_sha is None or last_audited_sha is None:
        return None
    return current_sha != last_audited_sha


def _freshness_state(link: dict[str, JsonValue] | None, stale: bool | None) -> ReportFreshnessState:
    if link is None:
        return "unknown"
    if stale is None:
        return "unknown"
    if stale is True:
        return "stale"
    return "fresh"


def _task_lifecycle_state(active_run: dict[str, JsonValue] | None) -> ReportTaskLifecycleState:
    if active_run is None:
        return "none"
    if _normalized_run_status(_active_run_value(active_run, "status")) in FAILED_REPORT_WAIT_STATUSES:
        return "failed"
    if _active_run_value(active_run, "startedAt") is not None:
        return "running"
    return "queued"


def _link_value(link: dict[str, JsonValue] | None, key: str) -> str | None:
    if link is None:
        return None
    return json_str(link.get(key))


def _active_run_value(active_run: dict[str, JsonValue] | None, key: str) -> str | None:
    if active_run is None:
        return None
    return json_str(active_run.get(key))


def active_runs_for_action(active_runs: list[JsonValue], action_key: str) -> list[JsonValue]:
    return [run for run in active_runs if _active_run_matches_action(run, action_key)]


def _active_run_matches_action(run: JsonValue, action_key: str) -> bool:
    if not isinstance(run, dict):
        return False
    return _active_run_action_key(run) == action_key


def _active_run_action_key(run: dict[str, JsonValue]) -> str | None:
    action_key = json_str(run.get("actionKey"))
    if action_key is not None:
        return action_key
    task = run.get("task")
    if not isinstance(task, dict):
        return None
    return json_str(task.get("actionKey"))


def report_wait_payload(
    repo_id: str,
    status: ReportStatusPayload,
    started_at: float,
    *,
    timed_out: bool,
) -> ReportWaitPayload:
    stale = _stale_report_audits(status["items"])
    failed = _failed_report_audits(status["items"])
    readable = _audits_with_readable_reports(status["items"])
    active = _audits_with_active_tasks(status["items"])
    queued = _audits_with_task_lifecycle(status["items"], "queued")
    running = _audits_with_task_lifecycle(status["items"], "running")
    missing = _audits_with_unreadable_reports(status["items"])
    fresh = not stale
    complete = not active and not missing and not failed and not timed_out
    return {
        "repo_id": repo_id,
        "complete": complete,
        "fresh": fresh,
        "timed_out": timed_out,
        "reason": _report_wait_reason(status, failed=failed, timed_out=timed_out),
        "elapsed_seconds": round(time.monotonic() - started_at),
        "current_head_sha": status["current_head_sha"],
        "last_report_at": status["last_report_at"],
        "counts": {
            "total": len(status["items"]),
            "readable": len(readable),
            "active": len(active),
            "queued": len(queued),
            "running": len(running),
            "missing": len(missing),
            "stale": len(stale),
            "failed": len(failed),
        },
        "readable": readable,
        "active": active,
        "queued": queued,
        "running": running,
        "missing": missing,
        "stale": stale,
        "failed": failed,
        "items": status["items"],
    }


def _report_wait_reason(
    status: ReportStatusPayload,
    *,
    failed: list[str],
    timed_out: bool,
) -> ReportWaitReason:
    if failed:
        return "failed"
    if timed_out:
        return "timeout"
    if status["active"] or status["queued"]:
        return "waiting"
    if status["missing"]:
        return "missing"
    if status["complete"]:
        return "complete"
    return "stale"


def _stale_report_audits(reports: list[ReportAuditStatusPayload]) -> list[str]:
    return [report["audit"] for report in reports if report["report"]["stale"] is True]


def _failed_report_audits(reports: list[ReportAuditStatusPayload]) -> list[str]:
    return [report["audit"] for report in reports if report["task"]["lifecycle_state"] == "failed"]


def _audits_with_readable_reports(reports: list[ReportAuditStatusPayload]) -> list[str]:
    return [report["audit"] for report in reports if report["report"]["can_read"]]


def _audits_with_active_tasks(reports: list[ReportAuditStatusPayload]) -> list[str]:
    return [report["audit"] for report in reports if report["task"]["active"]]


def _audits_with_unreadable_reports(reports: list[ReportAuditStatusPayload]) -> list[str]:
    return [report["audit"] for report in reports if not report["report"]["can_read"]]


def _audits_with_task_lifecycle(
    reports: list[ReportAuditStatusPayload],
    lifecycle_state: ReportTaskLifecycleState,
) -> list[str]:
    return [report["audit"] for report in reports if report["task"]["lifecycle_state"] == lifecycle_state]


def _normalized_run_status(value: str | None) -> str | None:
    return value.strip().lower() if value is not None else None


def validate_wait_options(poll_seconds: int, timeout_seconds: int) -> None:
    if poll_seconds < 1:
        raise ValueError("poll_seconds must be at least 1")
    if timeout_seconds < poll_seconds:
        raise ValueError("timeout_seconds must be greater than or equal to poll_seconds")


def validate_report_wait_options(options: ReportWaitOptions) -> None:
    validate_wait_options(options.poll_seconds, options.timeout_seconds)
    if options.heartbeat_seconds < 1:
        raise ValueError("heartbeat_seconds must be at least 1")


def score_grade(score: float) -> ScoreGrade:
    if score < SCORE_POOR_THRESHOLD:
        return "critical"
    if score < SCORE_FAIR_THRESHOLD:
        return "poor"
    if score < SCORE_GOOD_THRESHOLD:
        return "fair"
    if score < SCORE_EXCELLENT_THRESHOLD:
        return "good"
    return "excellent"


def _score_number(value: JsonValue) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _numeric_scores(scores: JsonObjectPayload) -> list[tuple[str, float]]:
    numeric_scores: list[tuple[str, float]] = []
    for axis, value in scores.items():
        score = _score_number(value)
        if score is not None:
            numeric_scores.append((axis, score))
    return numeric_scores


def score_grades(scores: JsonObjectPayload) -> dict[str, ScoreGrade]:
    return {axis: score_grade(score) for axis, score in _numeric_scores(scores)}


def score_summary(scores: JsonObjectPayload) -> ScoreSummaryPayload:
    numeric_scores = _numeric_scores(scores)
    if not numeric_scores:
        return {
            "overall_score": None,
            "overall_grade": None,
            "weakest_axis": None,
            "weakest_score": None,
            "weakest_grade": None,
        }

    weakest_axis, weakest_score = min(numeric_scores, key=lambda item: item[1])
    overall_score = round(sum(score for _, score in numeric_scores) / len(numeric_scores), 1)
    return {
        "overall_score": overall_score,
        "overall_grade": score_grade(overall_score),
        "weakest_axis": weakest_axis,
        "weakest_score": weakest_score,
        "weakest_grade": score_grade(weakest_score),
    }


def sort_project_repos(projects: list[ProjectRuntimeStatusPayload], sort: RepoSort) -> None:
    if sort == "default":
        return
    for project in projects:
        if sort == "name":
            project["repos"].sort(key=_repo_name_sort_key)
        elif sort == "weakest":
            project["repos"].sort(key=_repo_weakest_sort_key)
        elif sort == "overall":
            project["repos"].sort(key=_repo_overall_sort_key)
        elif sort == "latest-report":
            project["repos"].sort(key=_repo_latest_report_sort_key)
        else:
            raise ValueError(f"unknown repo sort: {sort}")


def _repo_name_sort_key(repo: RepoRuntimeStatusPayload) -> str:
    return (repo["github_repo"] or repo["repo_id"]).lower()


def _repo_weakest_sort_key(repo: RepoRuntimeStatusPayload) -> tuple[float, str]:
    return (_missing_last_score(repo["score_summary"]["weakest_score"]), _repo_name_sort_key(repo))


def _repo_overall_sort_key(repo: RepoRuntimeStatusPayload) -> tuple[float, str]:
    return (_missing_last_score(repo["score_summary"]["overall_score"]), _repo_name_sort_key(repo))


def _repo_latest_report_sort_key(repo: RepoRuntimeStatusPayload) -> tuple[bool, float, str]:
    timestamp = report_timestamp(repo["last_report_at"])
    return (timestamp is None, -(timestamp or 0.0), _repo_name_sort_key(repo))


def _missing_last_score(score: float | None) -> float:
    return score if score is not None else float("inf")


def last_report_at(reports: list[ReportAuditStatusPayload]) -> str | None:
    latest: tuple[float, str] | None = None
    for report in reports:
        artifact = report["report"]
        for value in (artifact["completed_at"], artifact["started_at"], artifact["created_at"]):
            if value is None:
                continue
            timestamp = report_timestamp(value)
            if timestamp is not None and (latest is None or timestamp > latest[0]):
                latest = (timestamp, value)
    return latest[1] if latest is not None else None


def report_timestamp(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def empty_report_status(repo_id: str, catalog: AuditCatalog) -> ReportStatusPayload:
    reports = [_empty_report_audit_status(audit) for audit in catalog.published_audits]
    return {
        "schema_version": 2,
        "repo_id": repo_id,
        "current_head_sha": None,
        "last_report_at": None,
        "complete": False,
        "fresh": True,
        "readable": False,
        "active": False,
        "queued": False,
        "running": False,
        "missing": True,
        "stale": False,
        "failed": False,
        "counts": {
            "total": len(reports),
            "readable": 0,
            "active": 0,
            "queued": 0,
            "running": 0,
            "missing": len(reports),
            "stale": 0,
            "failed": 0,
        },
        "items": reports,
    }


def _empty_report_audit_status(audit: AuditDefinition) -> ReportAuditStatusPayload:
    return {
        "audit": audit.action_key,
        "label": audit.title,
        "action_key": audit.action_key,
        "metric_group": audit.metric_group,
        "report": {
            "readability_state": "unavailable",
            "can_read": False,
            "freshness_state": "unknown",
            "current_head_sha": None,
            "audited_head_sha": None,
            "created_at": None,
            "started_at": None,
            "completed_at": None,
            "run_status": None,
            "fleet_task_id": None,
            "stale": None,
        },
        "task": {
            "lifecycle_state": "none",
            "active": False,
            "fleet_task_id": None,
            "run_status": None,
            "created_at": None,
            "started_at": None,
            "completed_at": None,
        },
        "agent_action": audit.action_key,
    }


def current_active_runs(payload: JsonObjectPayload) -> list[JsonValue]:
    return [run for run in json_list(payload.get("activeRuns")) if run_is_active(run)]


def active_runs_by_action_map(active_runs: list[JsonValue]) -> dict[str, dict[str, JsonValue]]:
    runs_by_action: dict[str, dict[str, JsonValue]] = {}
    for run in active_runs:
        if not isinstance(run, dict):
            continue
        action_key = _active_run_action_key(run)
        if action_key is None:
            continue
        runs_by_action.setdefault(action_key, run)
    return runs_by_action


def run_is_active(run: JsonValue) -> bool:
    if not isinstance(run, dict):
        return False
    if json_str(run.get("completedAt")) is not None:
        return False
    status = json_str(run.get("status"))
    return status not in TERMINAL_RUN_STATUSES
