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


def test_atomic_json_rejects_unserializable_payload_before_creating_files(tmp_path: Path) -> None:
    destination = tmp_path / "state" / "value.json"

    with pytest.raises(TypeError, match="not JSON serializable"):
        atomic_json.write_atomic_json(destination, {"value": object()})

    assert not destination.parent.exists()


@pytest.mark.parametrize(
    "target_operation",
    [
        "parent_directory_mkdir",
        "parent_directory_chmod",
        "temporary_file",
        "write",
        "file_fsync",
        "temporary_chmod",
        "rename",
        "destination_chmod",
        "parent_directory_open",
        "parent_directory_fsync",
        "parent_directory_close",
    ],
)
def test_atomic_json_exposes_each_durable_write_boundary(tmp_path: Path, target_operation: str) -> None:
    destination = tmp_path / "state" / "value.json"

    def failpoint(operation: str) -> None:
        if operation == target_operation:
            raise OSError(f"injected {target_operation}")

    with pytest.raises(OSError, match=f"injected {target_operation}"):
        atomic_json.write_atomic_json(destination, {"value": 1}, failpoint=failpoint)
