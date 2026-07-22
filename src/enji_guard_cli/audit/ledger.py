"""Durable local ledger for runs started before upstream projections catch up."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from enji_guard_cli.atomic_json import write_atomic_json
from enji_guard_cli.audit.errors import AuditMalformedError, AuditNotFoundError, AuditUpstreamError
from enji_guard_cli.audit.lifecycle import is_active_run, is_terminal_status, representative_projection
from enji_guard_cli.audit.ports import AuditLedgerEntry, AuditLedgerPort, AuditRun, AuditRunStart, AuditTaskDetail


@dataclass(frozen=True, slots=True)
class _EntryReconciliation:
    retained: bool
    suppress_task_id: str | None = None
    projected: AuditRun | None = None


@dataclass(frozen=True, slots=True)
class _TaskLookupResult:
    detail: AuditTaskDetail | None = None
    error: BaseException | None = None


class FileAuditLedger(AuditLedgerPort):
    """Small atomic JSON-backed implementation of :class:`AuditLedgerPort`."""

    def __init__(self, path: Path, *, ttl_seconds: int = 21_600, lookup_grace_seconds: int = 300) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.lookup_grace_seconds = lookup_grace_seconds

    def record_started(self, entry: AuditLedgerEntry) -> None:
        entries = [item for item in self._read() if not _same_identity(item, entry)]
        self._write((*entries, entry))

    def active_for(
        self,
        repo_id: str,
        audit_key: str | None = None,
        *,
        now: datetime | None = None,
    ) -> tuple[AuditLedgerEntry, ...]:
        point = _utc(now)
        entries = tuple(
            entry
            for entry in self._read()
            if entry.repo_id == repo_id
            and (audit_key is None or entry.audit_key == audit_key)
            and not _expired(entry, point)
            and not is_terminal_status(entry.task_status)
        )
        self.prune(now=point)
        return entries

    def reconcile(
        self,
        repo_id: str,
        upstream: Sequence[AuditRun],
        task_lookup: Callable[[str], AuditTaskDetail],
        *,
        now: datetime | None = None,
    ) -> tuple[AuditRun, ...]:
        point = _utc(now)
        entries = list(self._read())
        projected: list[AuditRun] = []
        suppress_task_ids: set[str] = set()
        upstream_by_action: dict[str, list[AuditRun]] = {}
        for run in upstream:
            if run.action_key is not None:
                upstream_by_action.setdefault(run.action_key, []).append(run)
        retained: list[AuditLedgerEntry] = []
        changed = False
        lookup_cache: dict[str, _TaskLookupResult] = {}
        for entry in entries:
            if entry.repo_id != repo_id:
                retained.append(entry)
                continue
            if _expired(entry, point) or is_terminal_status(entry.task_status):
                changed = True
                continue

            outcome = self._reconcile_entry(
                entry,
                task_lookup,
                lookup_cache,
                point,
                has_upstream=any(is_active_run(run) for run in upstream_by_action.get(entry.audit_key, [])),
            )
            if outcome.suppress_task_id is not None:
                suppress_task_ids.add(outcome.suppress_task_id)
            if not outcome.retained:
                changed = True
                continue
            retained.append(entry)
            if outcome.projected is not None:
                projected.append(outcome.projected)

        projected.extend(run for run in upstream if run.task_id is None or run.task_id not in suppress_task_ids)
        if changed:
            self._write(tuple(retained))
        return _dedupe_runs(projected)

    def _reconcile_entry(
        self,
        entry: AuditLedgerEntry,
        task_lookup: Callable[[str], AuditTaskDetail],
        lookup_cache: dict[str, _TaskLookupResult],
        now: datetime,
        *,
        has_upstream: bool,
    ) -> _EntryReconciliation:
        if entry.task_id is None:
            # Older start responses may not contain a task id. Preserve the
            # conservative action-level guard for that one legacy shape.
            return _EntryReconciliation(True, projected=None if has_upstream else _project(entry, None))

        # task_id is the identity boundary. Always refresh it, even when
        # active-runs already contains a row for the same action.
        result = lookup_cache.get(entry.task_id)
        if result is None:
            try:
                result = _TaskLookupResult(detail=task_lookup(entry.task_id))
            except (
                AuditNotFoundError,
                AuditUpstreamError,
                AuditMalformedError,
            ) as exc:
                result = _TaskLookupResult(error=exc)
            lookup_cache[entry.task_id] = result
        if result.error is not None:
            age = (now - entry.observed_at).total_seconds()
            if isinstance(result.error, AuditNotFoundError) and age > self.lookup_grace_seconds:
                return _EntryReconciliation(False)
            return _EntryReconciliation(True, entry.task_id, _project(entry, None))

        detail_run = _project(entry, result.detail)
        if not is_active_run(detail_run):
            return _EntryReconciliation(False, entry.task_id)
        return _EntryReconciliation(True, entry.task_id, detail_run)

    def prune(
        self,
        *,
        now: datetime | None = None,
        current_head_sha: str | None = None,
        audited_head_shas: dict[str, str] | None = None,
    ) -> int:
        point = _utc(now)
        entries = self._read()
        retained = tuple(
            entry
            for entry in entries
            if not _expired(entry, point)
            and not is_terminal_status(entry.task_status)
            and not _fresh_for(entry, current_head_sha, audited_head_shas)
        )
        removed = len(entries) - len(retained)
        if removed:
            self._write(retained)
        return removed

    def _read(self) -> tuple[AuditLedgerEntry, ...]:
        try:
            payload = cast(object, json.loads(self.path.read_text(encoding="utf-8")))
        except OSError, json.JSONDecodeError:
            return ()
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            return ()
        result: list[AuditLedgerEntry] = []
        for item in payload["entries"]:
            entry = _decode(item)
            if entry is not None:
                result.append(entry)
        return tuple(result)

    def _write(self, entries: Sequence[AuditLedgerEntry]) -> None:
        write_atomic_json(self.path, {"entries": [_encode(entry) for entry in entries]})


def new_entry(
    started: AuditRunStart,
    *,
    observed_at: datetime,
    started_at: str | None = None,
    ttl_seconds: int = 21600,
) -> AuditLedgerEntry:
    observed = _utc(observed_at)
    return AuditLedgerEntry(
        repo_id=started.repo_id,
        project_id=started.project_id,
        audit_key=started.action_key,
        task_id=started.task_id,
        task_status=started.task_status,
        current_head_sha=started.current_head_sha,
        audited_head_sha=started.last_audited_head_sha,
        observed_at=observed,
        started_at=started_at,
        expires_at=observed + timedelta(seconds=ttl_seconds),
    )


def _project(entry: AuditLedgerEntry, detail: AuditTaskDetail | None) -> AuditRun:
    return AuditRun(
        task_id=(detail.task_id if detail else None) or entry.task_id,
        action_key=entry.audit_key,
        status=(detail.status if detail else None) or entry.task_status,
        created_at=(detail.created_at if detail else None) or entry.observed_at.isoformat(),
        started_at=(detail.started_at if detail else None) or entry.started_at,
        completed_at=detail.completed_at if detail else None,
        projection_source="local_started_task_ledger",
        projection_status_source="task_by_id" if detail else "ledger",
        expires_at=entry.expires_at.isoformat(),
        current_head_sha=entry.current_head_sha,
        last_audited_head_sha=entry.audited_head_sha,
    )


def _same_identity(left: AuditLedgerEntry, right: AuditLedgerEntry) -> bool:
    if left.repo_id != right.repo_id:
        return False
    if left.audit_key != right.audit_key:
        return False
    # Task ids are the durable identity.  Entries without an id are the only
    # legacy case where action-level replacement is safe.
    if left.task_id is None or right.task_id is None:
        return left.task_id is None and right.task_id is None
    return left.task_id == right.task_id


def _dedupe_runs(runs: Sequence[AuditRun]) -> tuple[AuditRun, ...]:
    grouped: dict[tuple[str | None, str | None], list[AuditRun]] = {}
    for run in runs:
        identity = (run.task_id, None) if run.task_id is not None else (None, run.action_key)
        grouped.setdefault(identity, []).append(run)
    return tuple(representative_projection(group) for group in grouped.values())


def _expired(entry: AuditLedgerEntry, now: datetime) -> bool:
    return entry.expires_at <= now


def _fresh_for(
    entry: AuditLedgerEntry,
    current_head_sha: str | None,
    audited_head_shas: dict[str, str] | None,
) -> bool:
    audited = audited_head_shas.get(entry.audit_key) if audited_head_shas else entry.audited_head_sha
    return current_head_sha is not None and audited is not None and current_head_sha == audited


def _utc(value: datetime | None) -> datetime:
    point = value or datetime.now(UTC)
    return point.astimezone(UTC) if point.tzinfo is not None else point.replace(tzinfo=UTC)


def _encode(entry: AuditLedgerEntry) -> dict[str, object]:
    return {
        "repo_id": entry.repo_id,
        "project_id": entry.project_id,
        "audit_key": entry.audit_key,
        "task_id": entry.task_id,
        "task_status": entry.task_status,
        "current_head_sha": entry.current_head_sha,
        "audited_head_sha": entry.audited_head_sha,
        "observed_at": entry.observed_at.isoformat(),
        "started_at": entry.started_at,
        "expires_at": entry.expires_at.isoformat(),
    }


def _decode(value: object) -> AuditLedgerEntry | None:
    if not isinstance(value, dict):
        return None
    required = ("repo_id", "project_id", "audit_key", "observed_at", "expires_at")
    if any(not isinstance(value.get(key), str) or not value[key] for key in required):
        return None
    try:
        observed = datetime.fromisoformat(value["observed_at"])
        expires = datetime.fromisoformat(value["expires_at"])
    except TypeError, ValueError:
        return None
    return AuditLedgerEntry(
        repo_id=value["repo_id"],
        project_id=value["project_id"],
        audit_key=value["audit_key"],
        task_id=value.get("task_id") if isinstance(value.get("task_id"), str) else None,
        task_status=value.get("task_status") if isinstance(value.get("task_status"), str) else None,
        current_head_sha=value.get("current_head_sha") if isinstance(value.get("current_head_sha"), str) else None,
        audited_head_sha=value.get("audited_head_sha") if isinstance(value.get("audited_head_sha"), str) else None,
        observed_at=_utc(observed),
        started_at=value.get("started_at") if isinstance(value.get("started_at"), str) else None,
        expires_at=_utc(expires),
    )
