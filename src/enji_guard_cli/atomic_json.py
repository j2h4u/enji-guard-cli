"""Durable atomic JSON persistence shared by local file adapters."""

from __future__ import annotations

import json
from contextlib import suppress
from os import O_RDONLY, close, fsync
from os import open as os_open
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_atomic_json(path: Path, payload: object) -> None:
    """Replace *path* atomically after syncing file content and directory metadata."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            fsync(temporary.fileno())
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
        _fsync_directory(path.parent)
    except OSError:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink()
        raise


def _fsync_directory(path: Path) -> None:
    directory_fd = os_open(path, O_RDONLY)
    try:
        fsync(directory_fd)
    finally:
        close(directory_fd)
