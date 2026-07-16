from typer.testing import CliRunner

from enji_guard_cli.delivery.cli.app import app


def test_full_operator_tree_and_report_break() -> None:
    out = CliRunner().invoke(app, ["--help"])
    assert out.exit_code == 0
    for command in ("auth", "project", "repo", "recon", "audit", "schedule", "improvement-jobs", "email", "language"):
        assert f"│ {command}" in out.stdout
    assert "│ report " not in out.stdout


def test_audit_help_exposes_read_summary_start_wait() -> None:
    runner = CliRunner()
    output = runner.invoke(app, ["audit", "--help"])
    assert output.exit_code == 0
    for command in ("read", "summary", "start", "wait"):
        assert command in output.stdout


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
