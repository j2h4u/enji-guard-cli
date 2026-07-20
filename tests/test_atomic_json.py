from pathlib import Path

import pytest

from enji_guard_cli import atomic_json


def test_atomic_json_syncs_file_and_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced_descriptors: list[int] = []
    monkeypatch.setattr(atomic_json, "fsync", synced_descriptors.append)
    destination = tmp_path / "state" / "value.json"

    atomic_json.write_atomic_json(destination, {"value": 1})

    assert destination.read_text(encoding="utf-8") == '{"value": 1}\n'
    assert destination.stat().st_mode & 0o777 == 0o600
    assert len(synced_descriptors) == 2


def test_atomic_json_propagates_write_failure_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "state" / "value.json"

    def fail_sync(_descriptor: int) -> None:
        raise OSError("sync failed")

    monkeypatch.setattr(atomic_json, "fsync", fail_sync)

    with pytest.raises(OSError, match="sync failed"):
        atomic_json.write_atomic_json(destination, {"value": 1})

    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []
