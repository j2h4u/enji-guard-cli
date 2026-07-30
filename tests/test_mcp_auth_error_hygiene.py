"""Auth remediation is a CLI affordance, never MCP output.

The operator-facing first-run guidance names a host filesystem path and the
exact shell commands that repair it.  That is precisely the operator control
surface `AGENTS.md` forbids MCP from exposing, so the two surfaces must
diverge here even though they share one application runner.
"""

from pathlib import Path

from typer.testing import CliRunner

from enji_guard_cli.application.errors import ApplicationCommandError
from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.service_composition import mcp_query_facade
from enji_guard_cli.settings import DEFAULT_REPO_SORT


def _mcp_auth_error(auth_file: Path) -> ApplicationCommandError:
    """Provoke the real MCP auth failure through the composed query surface."""
    with mcp_query_facade(auth_file) as queries:
        try:
            queries.portfolio_overview(None, DEFAULT_REPO_SORT)
        except ApplicationCommandError as exc:
            return exc
    raise AssertionError("a missing credential must fail the MCP query surface")


def test_mcp_auth_error_hides_the_credential_path(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"

    error = _mcp_auth_error(auth_file)

    assert error.code == "AUTH_REQUIRED"
    assert error.message == "auth file does not exist"
    assert str(auth_file) not in error.message
    assert str(tmp_path) not in error.message


def test_mcp_auth_error_hides_cli_bootstrap_commands(tmp_path: Path) -> None:
    message = _mcp_auth_error(tmp_path / "auth.json").message

    assert "enji-guard auth" not in message
    assert "import-bearer" not in message
    assert "import-cookie" not in message
    assert "mkdir -p" not in message
    assert "chmod" not in message


def test_cli_auth_error_still_carries_the_remediation(tmp_path: Path) -> None:
    """The same failure, on the operator surface, must stay actionable."""
    auth_file = tmp_path / "auth.json"

    result = CliRunner().invoke(app, ["--auth-file", str(auth_file), "status"])

    assert result.exit_code == 3
    assert str(auth_file) in result.stderr
    assert "enji-guard auth import-bearer --stdin" in result.stderr
