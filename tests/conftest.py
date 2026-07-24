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
