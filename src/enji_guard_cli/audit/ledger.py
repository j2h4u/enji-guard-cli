"""Durable local ledger for runs started before upstream projections catch up."""

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from enji_guard_cli.audit.ports import AuditLedgerEntry, AuditLedgerPort, AuditRun, AuditTaskDetail

TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled", "cancelled", "skipped"})


class FileAuditLedger(AuditLedgerPort):
    """Small atomic JSON-backed implementation of :class:`AuditLedgerPort`."""

    def __init__(self, path: Path, *, ttl_seconds: int = 21_600, lookup_grace_seconds: int = 300) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.lookup_grace_seconds = lookup_grace_seconds

    def record_started(self, entry: AuditLedgerEntry) -> None:
        entries = [
            item for item in self._read() if not (item.repo_id == entry.repo_id and item.audit_key == entry.audit_key)
        ]
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
            and not _terminal(entry.task_status)
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
        upstream_by_key = {run.action_key for run in upstream if run.action_key is not None}
        projected: list[AuditRun] = list(upstream)
        retained: list[AuditLedgerEntry] = []
        changed = False
        for entry in entries:
            if entry.repo_id != repo_id:
                retained.append(entry)
                continue
            if _expired(entry, point) or _terminal(entry.task_status):
                changed = True
                continue
            if entry.audit_key in upstream_by_key:
                retained.append(entry)
                continue
            run = _lookup(entry, task_lookup, point, self.lookup_grace_seconds)
            if run is None:
                changed = True
                continue
            retained.append(entry)
            projected.append(run)
        if changed:
            self._write(tuple(retained))
        return tuple(projected)

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
            and not _terminal(entry.task_status)
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
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"entries": [_encode(entry) for entry in entries]}, handle, sort_keys=True)
            handle.write("\n")
        temporary.chmod(0o600)
        temporary.replace(self.path)


def new_entry(  # noqa: PLR0913
    *,
    repo_id: str,
    project_id: str,
    audit_key: str,
    task_id: str | None,
    task_status: str | None,
    current_head_sha: str | None,
    audited_head_sha: str | None,
    observed_at: datetime,
    started_at: str | None = None,
    ttl_seconds: int = 21_600,
) -> AuditLedgerEntry:
    observed = _utc(observed_at)
    return AuditLedgerEntry(
        repo_id=repo_id,
        project_id=project_id,
        audit_key=audit_key,
        task_id=task_id,
        task_status=task_status,
        current_head_sha=current_head_sha,
        audited_head_sha=audited_head_sha,
        observed_at=observed,
        started_at=started_at,
        expires_at=observed + timedelta(seconds=ttl_seconds),
    )


def _lookup(
    entry: AuditLedgerEntry,
    task_lookup: Callable[[str], AuditTaskDetail],
    now: datetime,
    grace_seconds: int,
) -> AuditRun | None:
    if entry.task_id is None:
        return _project(entry, None)
    try:
        detail = task_lookup(entry.task_id)
    except LookupError, OSError, RuntimeError, ValueError:
        age = (now - entry.observed_at).total_seconds()
        return _project(entry, None) if age <= grace_seconds else None
    run = _project(entry, detail)
    return run if run is not None and not _terminal(run.status) and run.completed_at is None else None


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


def _expired(entry: AuditLedgerEntry, now: datetime) -> bool:
    return entry.expires_at <= now


def _fresh_for(
    entry: AuditLedgerEntry,
    current_head_sha: str | None,
    audited_head_shas: dict[str, str] | None,
) -> bool:
    audited = audited_head_shas.get(entry.audit_key) if audited_head_shas else entry.audited_head_sha
    return current_head_sha is not None and audited is not None and current_head_sha == audited


def _terminal(status: str | None) -> bool:
    return (status or "").strip().lower() in TERMINAL_STATUSES


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
