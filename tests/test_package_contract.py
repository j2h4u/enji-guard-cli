"""Regression tests for distribution-artifact path isolation."""

import tarfile
from pathlib import Path

import pytest
from scripts.package_contract import ContractError, _artifacts


def _valid_artifacts(dist_dir: Path) -> tuple[Path, Path]:
    dist_dir.mkdir()
    wheel = dist_dir / "enji_guard_cli-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel content is not inspected by _artifacts")
    sdist = dist_dir / "enji_guard_cli-1.0.0.tar.gz"
    with tarfile.open(sdist, "w:gz"):
        pass
    return wheel, sdist


def test_artifacts_resolve_relative_distribution_paths_before_isolated_cwd_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    wheel, sdist = _valid_artifacts(Path("dist"))
    expected_wheel = wheel.resolve()
    expected_sdist = sdist.resolve()

    actual_wheel, actual_sdist = _artifacts(Path("dist"))
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    monkeypatch.chdir(isolated)

    assert actual_wheel == expected_wheel
    assert actual_sdist == expected_sdist
    assert actual_wheel.exists()
    assert actual_sdist.exists()


def test_artifacts_reject_extra_files(tmp_path: Path) -> None:
    _valid_artifacts(tmp_path / "dist")
    (tmp_path / "dist" / "unexpected.txt").write_text("not an artifact", encoding="utf-8")

    with pytest.raises(ContractError, match="only one wheel and one sdist"):
        _artifacts(tmp_path / "dist")
