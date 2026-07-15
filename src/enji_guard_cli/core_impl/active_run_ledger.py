import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from enji_guard_cli.audit.ports import AuditRerunState, AuditRun, AuditTaskDetail, AuditTaskLink
from enji_guard_cli.core_impl.models import REPORT_ARTIFACT_SCHEMA, TERMINAL_RUN_STATUSES
from enji_guard_cli.core_impl.payloads import json_dict, json_object_list, json_str
from enji_guard_cli.core_impl.repo_status import current_head_sha, last_audited_head_sha, out_of_date, run_is_active
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue
from enji_guard_cli.settings import ActiveRunLedgerSettings

LOCAL_ACTIVE_RUN_SOURCE = "local_started_task_ledger"
TASK_LOOKUP_SOURCE = "task_by_id"
type GetTask = Callable[[str], JsonObjectPayload]


@dataclass(frozen=True, slots=True)
class ActiveRunLedgerEntry:
    repo_id: str
    project_id: str
    action_key: str
    task_id: str | None
    task_status: str | None
    current_head_sha: str | None
    last_audited_head_sha: str | None
    observed_at: str
    started_at: str | None
    expires_at: str


@dataclass(frozen=True, slots=True)
class ActiveRunLedger:
    entries: list[ActiveRunLedgerEntry]


@dataclass(frozen=True, slots=True)
class MergedActiveRunsRequest:
    repo_id: str
    upstream_active_runs: list[JsonValue]
    rerun_state: JsonObjectPayload | None
    task_links_payload: JsonObjectPayload
    get_task: GetTask
    settings: ActiveRunLedgerSettings
    now: datetime


@dataclass(frozen=True, slots=True)
class TypedMergedActiveRunsRequest:
    repo_id: str
    upstream_active_runs: tuple[AuditRun, ...]
    rerun_state: AuditRerunState
    task_links: tuple[AuditTaskLink, ...]
    get_task: Callable[[str], AuditTaskDetail]
    settings: ActiveRunLedgerSettings
    now: datetime


@dataclass(frozen=True, slots=True)
class NewActiveRunLedgerEntryRequest:
    repo_id: str
    project_id: str
    action_key: str
    task_id: str | None
    task_status: str | None
    current_head_sha: str | None
    last_audited_head_sha: str | None
    observed_at: datetime
    started_at: str | None
    ttl_seconds: int


def record_started_run(
    settings: ActiveRunLedgerSettings,
    entry: ActiveRunLedgerEntry,
) -> None:
    ledger = read_active_run_ledger(settings)
    retained = [
        existing
        for existing in ledger.entries
        if not (existing.repo_id == entry.repo_id and existing.action_key == entry.action_key)
    ]
    write_active_run_ledger(settings.state_file, ActiveRunLedger(entries=[*retained, entry]))


def merged_active_runs(request: MergedActiveRunsRequest) -> list[JsonValue]:
    ledger = read_active_run_ledger(request.settings)
    report_links = _report_links_by_action(request.task_links_payload)
    upstream_by_action = _active_runs_by_action(request.upstream_active_runs)
    retained_entries: list[ActiveRunLedgerEntry] = []
    projected_runs: list[JsonValue] = []
    changed = False
    for entry in ledger.entries:
        if entry.repo_id != request.repo_id:
            retained_entries.append(entry)
            continue
        if _entry_expired(entry, request.now) or _entry_fresh(entry, request.rerun_state, report_links):
            changed = True
            continue
        if entry.action_key in upstream_by_action:
            retained_entries.append(entry)
            continue
        task_run = _task_lookup_active_run(
            entry,
            get_task=request.get_task,
            now=request.now,
            lookup_grace_seconds=request.settings.lookup_grace_seconds,
        )
        if task_run is None:
            changed = True
            continue
        retained_entries.append(entry)
        projected_runs.append(task_run)
    if changed:
        write_active_run_ledger(request.settings.state_file, ActiveRunLedger(entries=retained_entries))
    return [*request.upstream_active_runs, *projected_runs]


def merged_active_run_models(request: TypedMergedActiveRunsRequest) -> tuple[AuditRun, ...]:
    """Reconcile the local ledger without crossing back through JSON payloads."""

    ledger = read_active_run_ledger(request.settings)
    report_links = {
        link.action_key: link
        for link in request.task_links
        if link.action_key is not None and link.artifact_schema_name == REPORT_ARTIFACT_SCHEMA
    }
    upstream_by_action = {run.action_key: run for run in request.upstream_active_runs if run.action_key is not None}
    retained_entries: list[ActiveRunLedgerEntry] = []
    projected_runs: list[AuditRun] = []
    changed = False
    for entry in ledger.entries:
        if entry.repo_id != request.repo_id:
            retained_entries.append(entry)
            continue
        if _entry_expired(entry, request.now) or _typed_entry_fresh(entry, request.rerun_state, report_links):
            changed = True
            continue
        if entry.action_key in upstream_by_action:
            retained_entries.append(entry)
            continue
        task_run = _typed_task_lookup_active_run(
            entry,
            get_task=request.get_task,
            now=request.now,
            lookup_grace_seconds=request.settings.lookup_grace_seconds,
        )
        if task_run is None:
            changed = True
            continue
        retained_entries.append(entry)
        projected_runs.append(task_run)
    if changed:
        write_active_run_ledger(request.settings.state_file, ActiveRunLedger(entries=retained_entries))
    return (*request.upstream_active_runs, *projected_runs)


def projected_active_run(entry: ActiveRunLedgerEntry) -> JsonObjectPayload:
    return {
        "actionKey": entry.action_key,
        "fleetTaskId": entry.task_id,
        "status": entry.task_status,
        "createdAt": entry.observed_at,
        "startedAt": entry.started_at,
        "completedAt": None,
        "projectionSource": LOCAL_ACTIVE_RUN_SOURCE,
        "projectionStatusSource": "ledger",
        "expiresAt": entry.expires_at,
        "currentHeadSha": entry.current_head_sha,
        "lastAuditedHeadSha": entry.last_audited_head_sha,
    }


def new_entry(request: NewActiveRunLedgerEntryRequest) -> ActiveRunLedgerEntry:
    expires_at = request.observed_at.astimezone(UTC) + timedelta(seconds=request.ttl_seconds)
    observed_at_text = request.observed_at.astimezone(UTC).isoformat()
    return ActiveRunLedgerEntry(
        repo_id=request.repo_id,
        project_id=request.project_id,
        action_key=request.action_key,
        task_id=request.task_id,
        task_status=request.task_status,
        current_head_sha=request.current_head_sha,
        last_audited_head_sha=request.last_audited_head_sha,
        observed_at=observed_at_text,
        started_at=request.started_at,
        expires_at=expires_at.isoformat(),
    )


def read_active_run_ledger(settings: ActiveRunLedgerSettings) -> ActiveRunLedger:
    try:
        payload = cast(object, json.loads(settings.state_file.read_text(encoding="utf-8")))
    except OSError, json.JSONDecodeError:
        return ActiveRunLedger(entries=[])
    if not isinstance(payload, dict):
        return ActiveRunLedger(entries=[])
    entries_payload = payload.get("entries")
    if not isinstance(entries_payload, list):
        return ActiveRunLedger(entries=[])
    entries: list[ActiveRunLedgerEntry] = []
    for item in entries_payload:
        if isinstance(item, dict):
            entry = _entry_from_payload(item)
            if entry is not None:
                entries.append(entry)
    return ActiveRunLedger(entries=entries)


def has_entries_for_repo(settings: ActiveRunLedgerSettings, repo_id: str) -> bool:
    return any(entry.repo_id == repo_id for entry in read_active_run_ledger(settings).entries)


def write_active_run_ledger(path: Path, ledger: ActiveRunLedger) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        json.dump({"entries": [asdict(entry) for entry in ledger.entries]}, temp_file, sort_keys=True)
        temp_file.write("\n")
    temp_path.chmod(0o600)
    temp_path.replace(path)


def _entry_from_payload(payload: dict[str, object]) -> ActiveRunLedgerEntry | None:
    repo_id = _required_str(payload.get("repo_id"))
    project_id = _required_str(payload.get("project_id"))
    action_key = _required_str(payload.get("action_key"))
    observed_at = _required_str(payload.get("observed_at"))
    expires_at = _required_str(payload.get("expires_at"))
    if repo_id is None or project_id is None or action_key is None or observed_at is None or expires_at is None:
        return None
    return ActiveRunLedgerEntry(
        repo_id=repo_id,
        project_id=project_id,
        action_key=action_key,
        task_id=_optional_str(payload.get("task_id")),
        task_status=_optional_str(payload.get("task_status")),
        current_head_sha=_optional_str(payload.get("current_head_sha")),
        last_audited_head_sha=_optional_str(payload.get("last_audited_head_sha")),
        observed_at=observed_at,
        started_at=_optional_str(payload.get("started_at")),
        expires_at=expires_at,
    )


def _report_links_by_action(payload: JsonObjectPayload) -> dict[str, dict[str, JsonValue]]:
    links_by_action: dict[str, dict[str, JsonValue]] = {}
    for link in json_object_list(payload.get("links")):
        action_key = json_str(link.get("actionKey"))
        artifact_schema = json_str(link.get("artifactSchemaName"))
        if action_key is not None and artifact_schema == REPORT_ARTIFACT_SCHEMA:
            links_by_action[action_key] = link
    return links_by_action


def _active_runs_by_action(active_runs: list[JsonValue]) -> dict[str, dict[str, JsonValue]]:
    by_action: dict[str, dict[str, JsonValue]] = {}
    for run in active_runs:
        if not isinstance(run, dict):
            continue
        action_key = json_str(run.get("actionKey"))
        if action_key is None:
            task = json_dict(run.get("task"))
            action_key = json_str(task.get("actionKey"))
        if action_key is None:
            continue
        by_action.setdefault(action_key, run)
    return by_action


def _entry_expired(entry: ActiveRunLedgerEntry, now: datetime) -> bool:
    expires_at = _parse_datetime(entry.expires_at)
    return expires_at is None or expires_at <= now.astimezone(UTC)


def _entry_fresh(
    entry: ActiveRunLedgerEntry,
    rerun_state: JsonObjectPayload | None,
    report_links: dict[str, dict[str, JsonValue]],
) -> bool:
    link = report_links.get(entry.action_key)
    if link is None:
        return False
    return out_of_date(current_head_sha(rerun_state), last_audited_head_sha(rerun_state, entry.action_key)) is False


def _typed_entry_fresh(
    entry: ActiveRunLedgerEntry,
    rerun_state: AuditRerunState,
    report_links: dict[str, AuditTaskLink],
) -> bool:
    if entry.action_key not in report_links:
        return False
    return (
        out_of_date(
            rerun_state.current_head_sha,
            rerun_state.audited_head_shas.get(entry.action_key),
        )
        is False
    )


def _task_lookup_active_run(
    entry: ActiveRunLedgerEntry,
    *,
    get_task: GetTask,
    now: datetime,
    lookup_grace_seconds: int,
) -> JsonObjectPayload | None:
    if entry.task_id is None:
        return projected_active_run(entry)
    try:
        payload = get_task(entry.task_id)
    except EnjiApiError:
        if _entry_age_seconds(entry, now) <= lookup_grace_seconds:
            return projected_active_run(entry)
        return None
    task = _task_payload(payload)
    active_run = {
        "actionKey": entry.action_key,
        "fleetTaskId": _optional_str(task.get("id")) or entry.task_id,
        "status": _optional_str(task.get("status")) or entry.task_status,
        "createdAt": _optional_str(task.get("createdAt")) or entry.observed_at,
        "startedAt": _optional_str(task.get("startedAt")) or entry.started_at,
        "completedAt": _optional_str(task.get("completedAt")),
        "projectionSource": LOCAL_ACTIVE_RUN_SOURCE,
        "projectionStatusSource": TASK_LOOKUP_SOURCE,
        "expiresAt": entry.expires_at,
        "currentHeadSha": entry.current_head_sha,
        "lastAuditedHeadSha": entry.last_audited_head_sha,
    }
    return active_run if run_is_active(active_run) else None


def _typed_task_lookup_active_run(
    entry: ActiveRunLedgerEntry,
    *,
    get_task: Callable[[str], AuditTaskDetail],
    now: datetime,
    lookup_grace_seconds: int,
) -> AuditRun | None:
    if entry.task_id is None:
        return _audit_run_from_ledger(entry)
    try:
        task = get_task(entry.task_id)
    except EnjiApiError:
        if _entry_age_seconds(entry, now) <= lookup_grace_seconds:
            return _audit_run_from_ledger(entry)
        return None
    active_run = AuditRun(
        task_id=task.task_id or entry.task_id,
        action_key=entry.action_key,
        status=task.status or entry.task_status,
        created_at=task.created_at or entry.observed_at,
        started_at=task.started_at or entry.started_at,
        completed_at=task.completed_at,
        projection_source=LOCAL_ACTIVE_RUN_SOURCE,
        projection_status_source=TASK_LOOKUP_SOURCE,
        expires_at=entry.expires_at,
        current_head_sha=entry.current_head_sha,
        last_audited_head_sha=entry.last_audited_head_sha,
    )
    return active_run if _typed_run_is_active(active_run) else None


def _audit_run_from_ledger(entry: ActiveRunLedgerEntry) -> AuditRun:
    return AuditRun(
        task_id=entry.task_id,
        action_key=entry.action_key,
        status=entry.task_status,
        created_at=entry.observed_at,
        started_at=entry.started_at,
        completed_at=None,
        projection_source=LOCAL_ACTIVE_RUN_SOURCE,
        projection_status_source="ledger",
        expires_at=entry.expires_at,
        current_head_sha=entry.current_head_sha,
        last_audited_head_sha=entry.last_audited_head_sha,
    )


def _typed_run_is_active(run: AuditRun) -> bool:
    return run.completed_at is None and (run.status or "").strip().lower() not in TERMINAL_RUN_STATUSES


def _task_payload(payload: JsonObjectPayload) -> Mapping[str, object]:
    task = payload.get("task")
    return task if isinstance(task, dict) else payload


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _entry_age_seconds(entry: ActiveRunLedgerEntry, now: datetime) -> int:
    observed_at = _parse_datetime(entry.observed_at)
    if observed_at is None:
        return 0
    return int((now.astimezone(UTC) - observed_at).total_seconds())


def _required_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
