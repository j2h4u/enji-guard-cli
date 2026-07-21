from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from enji_guard_cli.delivery.cli.app import app


def test_full_operator_tree_and_report_break() -> None:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    commands = root.commands
    for command in ("auth", "project", "repo", "recon", "audit", "schedule", "improvement-jobs", "email", "language"):
        assert command in commands
    assert "report" not in commands


def test_audit_help_exposes_read_summary_start_wait() -> None:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    audit = root.commands["audit"]
    assert isinstance(audit, TyperGroup)
    assert set(audit.commands) >= {"read", "summary", "start", "wait"}
    runner = CliRunner()
    # Reachability only; command membership above is the stable contract.
    assert runner.invoke(app, ["audit", "--help"]).exit_code == 0


def test_application_surface_is_typed() -> None:
    from enji_guard_cli.application import Application

    for method in (
        "audit_start",
        "audit_read",
        "audit_summary",
        "audit_wait",
        "set_schedules",
        "set_autofixes",
        "set_email_preferences",
        "set_language",
    ):
        assert callable(getattr(Application, method, None))
