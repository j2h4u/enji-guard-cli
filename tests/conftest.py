from __future__ import annotations

import functools
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


@functools.cache
def _docker_daemon_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "info"], capture_output=True, check=False)
    return probe.returncode == 0


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("docker") is not None and not _docker_daemon_available():
        pytest.skip("requires a reachable Docker daemon")


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every test out of the developer's real home directory.

    `settings.py` resolves the auth file under `Path.home()`, so an
    unpinned HOME lets a default-constructed service read or overwrite
    real credentials in ~/.config/enji-guard/auth.json.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
