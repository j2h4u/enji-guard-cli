"""``--json`` must stay machine-readable on every error path, not only on success."""

import json
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from enji_guard_cli.delivery.cli.app import app


def _error_envelope(stderr: str) -> dict[str, object]:
    payload = json.loads(stderr)
    assert isinstance(payload, dict)
    return payload


def test_json_auth_failure_emits_error_envelope(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["--json", "--auth-file", str(tmp_path / "missing.json"), "status", "github@github.com:owner/name"],
    )

    assert result.exit_code == 3
    envelope = _error_envelope(result.stderr)
    assert envelope["code"] == "AUTH_REQUIRED"
    assert isinstance(envelope["message"], str)


def test_human_auth_failure_keeps_code_message_line(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["--auth-file", str(tmp_path / "missing.json"), "status", "github@github.com:owner/name"],
    )

    assert result.exit_code == 3
    assert result.stderr.startswith("AUTH_REQUIRED: ")


def test_json_validation_failure_emits_error_envelope() -> None:
    result = CliRunner().invoke(app, ["--json", "email", "set", "--all-repos", "--all-projects", "--manual", "on"])

    assert result.exit_code == 1
    assert _error_envelope(result.stderr) == {
        "code": "VALIDATION",
        "message": "pass --all-repos or --all-projects, not both",
    }


def test_json_unready_health_emits_error_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(socket, "create_connection", unavailable)

    result = CliRunner().invoke(app, ["health", "--ready", "--json"])

    assert result.exit_code == 1
    envelope = _error_envelope(result.stderr)
    assert envelope["code"] == "UNREADY"
    assert envelope["message"] == "MCP listener is not ready: connection refused"


def test_json_auth_import_guard_emits_error_envelope() -> None:
    result = CliRunner().invoke(app, ["auth", "import-bearer", "--json"])

    assert result.exit_code == 1
    assert _error_envelope(result.stderr)["code"] == "VALIDATION"


def test_json_error_stream_never_pollutes_stdout(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["--json", "--auth-file", str(tmp_path / "missing.json"), "access"],
    )

    assert result.exit_code == 3
    assert result.stdout == ""
    assert _error_envelope(result.stderr)["code"] == "AUTH_REQUIRED"
