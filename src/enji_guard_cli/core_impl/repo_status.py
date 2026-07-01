import time
from datetime import UTC, datetime

from enji_guard_cli.audits import REPORT_AUDITS, AuditDefinition
from enji_guard_cli.core_impl.models import (
    FAILED_REPORT_WAIT_STATUSES,
    REPORT_ARTIFACT_SCHEMA,
    SCORE_EXCELLENT_THRESHOLD,
    SCORE_FAIR_THRESHOLD,
    SCORE_GOOD_THRESHOLD,
    SCORE_POOR_THRESHOLD,
    TERMINAL_RUN_STATUSES,
    ProjectRuntimeStatusPayload,
    ReportAuditState,
    ReportAuditStatusPayload,
    ReportStatusPayload,
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
) -> ReportStatusPayload:
    links_by_action = _report_links_by_action(payload)
    active_runs_by_action = active_runs_by_action_map(active_runs)
    current_sha = current_head_sha(rerun_state)
    reports = [
        _report_audit_status(audit, links_by_action, active_runs_by_action, current_sha, rerun_state)
        for audit in REPORT_AUDITS
    ]
    latest_report_at = last_report_at(reports)
    ready = [report["audit"] for report in reports if report["ready"]]
    running = [report["audit"] for report in reports if report["running"]]
    missing = [report["audit"] for report in reports if report["state"] == "missing"]
    return {
        "repo_id": repo_id,
        "current_head_sha": current_sha,
        "last_report_at": latest_report_at,
        "complete": not running and not missing,
        "ready": ready,
        "running": running,
        "missing": missing,
        "reports": reports,
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
    state = _report_audit_state(link, active_run)
    last_audited_sha = last_audited_head_sha(rerun_state, action_key)
    route_slug = audit.route_slug
    if route_slug is None:
        raise ValueError("report audit status cannot be built for recon")
    return {
        "audit": audit.alias.value,
        "label": audit.label,
        "action_key": action_key,
        "route_slug": route_slug,
        "state": state,
        "ready": state == "ready",
        "running": state == "running",
        "fleet_task_id": _active_run_value(active_run, "fleetTaskId") or _link_value(link, "fleetTaskId"),
        "created_at": _active_run_value(active_run, "createdAt") or _link_value(link, "createdAt"),
        "started_at": _active_run_value(active_run, "startedAt") or _link_value(link, "startedAt"),
        "completed_at": _active_run_value(active_run, "completedAt") or _link_value(link, "completedAt"),
        "run_status": _active_run_value(active_run, "status") or _link_value(link, "status"),
        "current_head_sha": current_sha,
        "last_audited_head_sha": last_audited_sha,
        "out_of_date": out_of_date(current_sha, last_audited_sha),
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


def _report_audit_state(
    link: dict[str, JsonValue] | None,
    active_run: dict[str, JsonValue] | None,
) -> ReportAuditState:
    if active_run is not None:
        return "running"
    if link is not None:
        return "ready"
    return "missing"


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
    options: ReportWaitOptions,
    timed_out: bool,
) -> ReportWaitPayload:
    stale = _stale_report_audits(status)
    failed = _failed_report_audits(status)
    fresh = not stale
    complete = status["complete"] and not failed and not timed_out
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
            "total": len(status["reports"]),
            "ready": len(status["ready"]),
            "running": len(status["running"]),
            "missing": len(status["missing"]),
            "stale": len(stale),
            "failed": len(failed),
        },
        "ready": status["ready"],
        "running": status["running"],
        "missing": status["missing"],
        "stale": stale,
        "failed": failed,
        "reports": status["reports"],
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
    if status["complete"]:
        return "complete"
    return "waiting"


def _stale_report_audits(status: ReportStatusPayload) -> list[str]:
    return [report["audit"] for report in status["reports"] if report["out_of_date"] is True]


def _failed_report_audits(status: ReportStatusPayload) -> list[str]:
    return [
        report["audit"]
        for report in status["reports"]
        if _normalized_run_status(report["run_status"]) in FAILED_REPORT_WAIT_STATUSES
    ]


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


def next_poll_sleep(deadline: float, poll_seconds: int) -> float:
    return max(0.0, min(float(poll_seconds), deadline - time.monotonic()))


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
        for value in (report["completed_at"], report["started_at"], report["created_at"]):
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


def empty_report_status(repo_id: str) -> ReportStatusPayload:
    reports = [_empty_report_audit_status(audit) for audit in REPORT_AUDITS]
    return {
        "repo_id": repo_id,
        "current_head_sha": None,
        "last_report_at": None,
        "complete": False,
        "ready": [],
        "running": [],
        "missing": [report["audit"] for report in reports],
        "reports": reports,
    }


def _empty_report_audit_status(audit: AuditDefinition) -> ReportAuditStatusPayload:
    route_slug = audit.route_slug
    if route_slug is None:
        raise ValueError("report audit status cannot be built for recon")
    return {
        "audit": audit.alias.value,
        "label": audit.label,
        "action_key": audit.action_key,
        "route_slug": route_slug,
        "state": "missing",
        "ready": False,
        "running": False,
        "fleet_task_id": None,
        "created_at": None,
        "started_at": None,
        "completed_at": None,
        "run_status": None,
        "current_head_sha": None,
        "last_audited_head_sha": None,
        "out_of_date": None,
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
