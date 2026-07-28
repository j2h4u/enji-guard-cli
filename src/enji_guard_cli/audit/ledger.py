"""Durable local ledger for runs started before upstream projections catch up."""

import contextlib
import fcntl
import json
import threading
from collections.abc import Callable, Iterator, Sequence
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


@dataclass(frozen=True, slots=True)
class _ReconciliationCommit:
    projected: tuple[AuditRun, ...]
    suppress_task_ids: frozenset[str]


# How many times reconcile re-reads the ledger looking for task ids that
# appeared while earlier lookups were in flight.  Each extra round costs one
# short locked read plus the new lookups; entries still unresolved after the
# last round are kept under the conservative ledger-only projection.
_MAX_LOOKUP_ROUNDS = 3


class FileAuditLedger(AuditLedgerPort):
    """Small atomic JSON-backed implementation of :class:`AuditLedgerPort`."""

    def __init__(self, path: Path, *, ttl_seconds: int = 21_600, lookup_grace_seconds: int = 300) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.lookup_grace_seconds = lookup_grace_seconds
        self._thread_lock = threading.RLock()
        self._depth = 0

    def record_started(self, entry: AuditLedgerEntry) -> None:
        with self._transaction():
            entries = [item for item in self._read() if not _same_identity(item, entry)]
            self._write((*entries, entry))

    def reconcile(
        self,
        repo_id: str,
        upstream: Sequence[AuditRun],
        task_lookup: Callable[[str], AuditTaskDetail],
        *,
        now: datetime | None = None,
    ) -> tuple[AuditRun, ...]:
        point = _utc(now)
        upstream_by_action: dict[str, list[AuditRun]] = {}
        for run in upstream:
            if run.action_key is not None:
                upstream_by_action.setdefault(run.action_key, []).append(run)

        # Lookups are network calls, so they run with no lock held.  Each round
        # takes the lock only long enough to read which task ids still need a
        # detail, then drops it again for the calls themselves.
        lookup_cache: dict[str, _TaskLookupResult] = {}
        for _ in range(_MAX_LOOKUP_ROUNDS):
            pending = self._pending_lookups(repo_id, point, lookup_cache)
            if not pending:
                break
            _resolve_lookups(pending, task_lookup, lookup_cache)

        commit = self._commit_reconciliation(repo_id, upstream_by_action, lookup_cache, point)
        projected = [
            *commit.projected,
            *(run for run in upstream if run.task_id is None or run.task_id not in commit.suppress_task_ids),
        ]
        return _dedupe_runs(projected)

    def _pending_lookups(
        self,
        repo_id: str,
        now: datetime,
        lookup_cache: dict[str, _TaskLookupResult],
    ) -> tuple[str, ...]:
        """Task ids this repository still needs a fresh detail for, in entry order."""

        with self._transaction():
            entries = self._read()
        pending: dict[str, None] = {}
        for entry in entries:
            if entry.task_id is None or entry.task_id in lookup_cache:
                continue
            if entry.repo_id != repo_id or _expired(entry, now) or is_terminal_status(entry.task_status):
                continue
            pending[entry.task_id] = None
        return tuple(pending)

    def _commit_reconciliation(
        self,
        repo_id: str,
        upstream_by_action: dict[str, list[AuditRun]],
        lookup_cache: dict[str, _TaskLookupResult],
        now: datetime,
    ) -> _ReconciliationCommit:
        """Apply the resolved lookups to a freshly read ledger, under one lock.

        Every decision is recomputed against the entries read inside this
        transaction, so a concurrent writer that added, replaced or removed an
        entry during the lookups is honoured rather than overwritten.  Lookup
        results are keyed by task id, which is the durable identity, so they
        stay valid for whatever entry now carries that id.
        """

        projected: list[AuditRun] = []
        suppress_task_ids: set[str] = set()
        retained: list[AuditLedgerEntry] = []
        changed = False
        with self._transaction():
            for entry in self._read():
                if entry.repo_id != repo_id:
                    retained.append(entry)
                    continue
                if _expired(entry, now) or is_terminal_status(entry.task_status):
                    changed = True
                    continue

                outcome = self._reconcile_entry(
                    entry,
                    lookup_cache.get(entry.task_id) if entry.task_id is not None else None,
                    now,
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

            if changed:
                self._write(tuple(retained))
        return _ReconciliationCommit(tuple(projected), frozenset(suppress_task_ids))

    def _reconcile_entry(
        self,
        entry: AuditLedgerEntry,
        result: _TaskLookupResult | None,
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
        if result is None:
            # The entry landed after the last lookup round.  Never drop a guard
            # we have no upstream answer for: keep it and project from the
            # ledger, exactly as a transient lookup failure does.
            return _EntryReconciliation(True, entry.task_id, _project(entry, None))
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
        with self._transaction():
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

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        """Serialize one read-modify-write cycle across threads and processes.

        The thread lock is reentrant, so a nested locked section stays on the
        already-held POSIX lock instead of blocking on its own file handle.
        """

        with self._thread_lock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with _lock_path(self.path).open("a", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                self._depth = 1
                try:
                    yield
                finally:
                    self._depth = 0
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

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


def _resolve_lookups(
    task_ids: Sequence[str],
    task_lookup: Callable[[str], AuditTaskDetail],
    lookup_cache: dict[str, _TaskLookupResult],
) -> None:
    """Fetch task details sequentially with no ledger lock held."""

    for task_id in task_ids:
        try:
            lookup_cache[task_id] = _TaskLookupResult(detail=task_lookup(task_id))
        except (
            AuditNotFoundError,
            AuditUpstreamError,
            AuditMalformedError,
        ) as exc:
            lookup_cache[task_id] = _TaskLookupResult(error=exc)


def _lock_path(path: Path) -> Path:
    return path.with_suffix(f"{path.suffix}.lock")


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
