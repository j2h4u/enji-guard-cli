import importlib

import pytest
from typer.testing import CliRunner

from enji_guard_cli import version as version_module
from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.version import _commit_from_vcs_version


def test_vcs_version_commit_is_recovered() -> None:
    assert _commit_from_vcs_version("1.0.1.dev2+g13920645c") == "13920645c"
    assert _commit_from_vcs_version("1.0.0") is None


def test_global_version_reports_semver_and_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_module, "COMMIT_SHA", "0123456789abcdef")
    monkeypatch.setattr(version_module, "package_version", lambda: "1.2.3")

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "enji-guard-cli 1.2.3 (commit 0123456789ab)\n"


def test_global_version_exits_before_application_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

    def fail_if_called() -> None:
        pytest.fail("application initialization must not run for --version")

    monkeypatch.setattr(app_module, "_close_cached_application", fail_if_called)

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
