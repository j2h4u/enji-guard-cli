"""Pin the documented exit-code contract so automation can branch on it."""

import importlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from application_builder import RecordingPortfolioGateway, recording_application
from enji_guard_cli.application import exit_code_for_error
from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.errors import EnjiApiError

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

README = Path(__file__).resolve().parent.parent / "README.md"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("AUTH_REQUIRED", 3),
        ("AUTH_INVALID", 3),
        ("AUTH_CORRUPT", 3),
        ("NOT_FOUND", 4),
        ("BAD_SELECTOR", 4),
        ("UPSTREAM", 1),
        ("STORAGE", 1),
        ("VALIDATION", 1),
    ],
)
def test_error_codes_map_to_documented_exit_codes(code: str, expected: int) -> None:
    assert exit_code_for_error(code) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [("AUTH_REQUIRED", 3), ("NOT_FOUND", 4), ("UPSTREAM", 1)],
)
def test_cli_propagates_the_application_exit_code(monkeypatch: pytest.MonkeyPatch, code: str, expected: int) -> None:
    """An upstream failure raised by the port keeps its code all the way out.

    The failure enters at the Portfolio gateway, so the real facade, the real
    application runner, and the CLI all take part in the translation.
    """
    application = recording_application(portfolio=RecordingPortfolioGateway(failure=EnjiApiError(code, "failed")))
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: application)

    result = CliRunner().invoke(app, ["status", "github@github.com:owner/name"])

    assert result.exit_code == expected
    assert result.stderr.startswith(f"{code}: failed")


def test_success_exits_zero() -> None:
    assert CliRunner().invoke(app, ["--help"]).exit_code == 0


def test_usage_error_exits_two() -> None:
    assert CliRunner().invoke(app, ["status", "--sort", "not-a-sort"]).exit_code == 2
    assert CliRunner().invoke(app, ["no-such-command"]).exit_code == 2


def test_validation_failure_exits_one() -> None:
    result = CliRunner().invoke(app, ["email", "set", "--all-repos", "--all-projects", "--manual", "on"])

    assert result.exit_code == 1


def test_missing_credentials_exit_three(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["--auth-file", str(tmp_path / "auth.json"), "status", "github@github.com:owner/name"]
    )

    assert result.exit_code == 3


def test_readme_documents_every_exit_code() -> None:
    readme = README.read_text(encoding="utf-8")
    section = readme.split("### Exit Codes", 1)
    assert len(section) == 2, "README must document the exit-code contract"
    table = section[1]
    for code in ("`0`", "`1`", "`2`", "`3`", "`4`"):
        assert code in table
