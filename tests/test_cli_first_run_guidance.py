"""The first error a new operator sees must name the file and the fix."""

from pathlib import Path

from typer.testing import CliRunner

from enji_guard_cli.delivery.cli.app import app


def _first_run_stderr(tmp_path: Path) -> str:
    auth_file = tmp_path / "auth.json"
    result = CliRunner().invoke(
        app,
        ["--auth-file", str(auth_file), "status", "github@github.com:owner/name"],
    )
    assert result.exit_code == 3
    return result.stderr


def test_auth_required_names_the_credential_file(tmp_path: Path) -> None:
    stderr = _first_run_stderr(tmp_path)

    assert stderr.startswith("AUTH_REQUIRED: ")
    assert str(tmp_path / "auth.json") in stderr


def test_auth_required_names_the_command_that_fixes_it(tmp_path: Path) -> None:
    stderr = _first_run_stderr(tmp_path)

    assert "enji-guard auth import-bearer --stdin" in stderr
    assert "enji-guard auth import-cookie --stdin" in stderr
    assert "enji-guard auth status" in stderr


def test_auth_required_mentions_the_mandatory_directory_prerequisite(tmp_path: Path) -> None:
    assert "mkdir -p ~/.config/enji-guard/logs" in _first_run_stderr(tmp_path)


def test_non_auth_errors_stay_free_of_credential_guidance(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["--auth-file", str(tmp_path / "auth.json"), "status", "--sort", "nope"])

    assert result.exit_code == 2
    assert "import-bearer" not in result.stderr
