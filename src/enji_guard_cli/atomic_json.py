"""Durable atomic JSON persistence shared by local file adapters."""

from __future__ import annotations

import json
from os import O_RDONLY, close, fsync
from os import open as os_open
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol


class AtomicJsonFailpoint(Protocol):
    """Inject a deterministic failure immediately before a durable operation."""

    def __call__(self, operation: str) -> None: ...


def write_atomic_json(
    path: Path,
    payload: object,
    *,
    indent: int | None = None,
    failpoint: AtomicJsonFailpoint | None = None,
) -> None:
    """Replace *path* atomically after syncing file content and directory metadata."""
    serialized = json.dumps(payload, indent=indent, sort_keys=True) + "\n"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary_path: Path | None = None
    try:
        _trigger(failpoint, "temporary_file")
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            _trigger(failpoint, "write")
            temporary.write(serialized)
            temporary.flush()
            _trigger(failpoint, "file_fsync")
            fsync(temporary.fileno())
        temporary_path.chmod(0o600)
        _trigger(failpoint, "rename")
        temporary_path.replace(path)
        path.chmod(0o600)
        # A failure here means the rename is visible but not proven durable.
        _fsync_directory(path.parent, failpoint=failpoint)
    except OSError as original_error:
        if temporary_path is not None:
            try:
                _trigger(failpoint, "unlink")
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                raise ExceptionGroup("atomic JSON persistence and cleanup failed", [original_error, cleanup_error]) from None
        raise


def fsync_directory(path: Path, *, failpoint: AtomicJsonFailpoint | None = None) -> None:
    """Sync a directory after a metadata mutation; failures are significant."""

    _fsync_directory(path, failpoint=failpoint)


def _fsync_directory(path: Path, *, failpoint: AtomicJsonFailpoint | None) -> None:
    _trigger(failpoint, "parent_directory_open")
    directory_fd = os_open(path, O_RDONLY)
    try:
        _trigger(failpoint, "parent_directory_fsync")
        fsync(directory_fd)
    finally:
        close(directory_fd)


def _trigger(failpoint: AtomicJsonFailpoint | None, operation: str) -> None:
    if failpoint is not None:
        failpoint(operation)
