from __future__ import annotations

import functools
import shutil
import subprocess
import sys
from collections.abc import Iterator
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
def plain_cli_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render CLI output without colour, at a stable width.

    Typer renders help through rich, which injects ANSI sequences *inside* an
    option name once colour is on -- so `"--ready" in rendered` is false even
    though the flag is there.  GitHub Actions sets `FORCE_COLOR`, which is why
    the help assertions passed locally and failed only in CI.  Pin the
    presentation instead of teaching every assertion to strip escapes.
    """
    for variable in ("FORCE_COLOR", "CLICOLOR_FORCE", "TERM"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "120")


@pytest.fixture(autouse=True)
def restored_telemetry_globals() -> Iterator[None]:
    """Undo `configure_logging` side effects on module globals.

    `runtime_observability.telemetry` keeps `_ACTIVE_SINK` and
    `_ACTIVE_PROVENANCE` as module state. Tests that call
    `configure_logging(...)` otherwise leak that state into whichever
    test the xdist scheduler happens to run next.
    """
    from enji_guard_cli.runtime_observability import telemetry

    active_sink = telemetry._ACTIVE_SINK
    active_provenance = telemetry._ACTIVE_PROVENANCE
    try:
        yield
    finally:
        leaked_sink = telemetry._ACTIVE_SINK
        if leaked_sink is not None and leaked_sink is not active_sink:
            leaked_sink.close()
        telemetry._ACTIVE_SINK = active_sink
        telemetry._ACTIVE_PROVENANCE = active_provenance


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
