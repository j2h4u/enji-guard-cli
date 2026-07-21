import json
import socket
import sys
from types import ModuleType

import pytest
from typer.testing import CliRunner

from enji_guard_cli.delivery.cli.app import app
from enji_guard_cli.runtime_observability.readiness import BackendReadinessState, ReadinessVerdict

cli_module: ModuleType = sys.modules["enji_guard_cli.delivery.cli.app"]


class _Connection:
    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _state(*, failure_code: str | None = None) -> BackendReadinessState:
    return BackendReadinessState(
        ready=failure_code is None,
        checked_at="2026-07-17T00:00:00+00:00",
        last_success_at=None,
        failure_kind="auth" if failure_code else None,
        failure_code=failure_code,
        failure_message="credentials are required" if failure_code else None,
        failure_status_code=401 if failure_code else None,
        credential_type=None,
        consecutive_failures=1 if failure_code else 0,
    )


def test_health_default_reports_process_health_without_readiness_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("socket used"))
    monkeypatch.setattr(cli_module, "readiness_verdict", lambda: pytest.fail("readiness used"))

    result = CliRunner().invoke(app, ["health", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"status": "ok"}
    assert result.stderr == ""


def test_health_ready_rejects_unavailable_mcp_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> _Connection:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(cli_module, "readiness_verdict", lambda: pytest.fail("backend checked before MCP"))

    result = CliRunner().invoke(app, ["health", "--ready"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "UNREADY: MCP listener is not ready: connection refused" in result.stderr


def test_health_ready_surfaces_cached_backend_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: _Connection())
    monkeypatch.setattr(
        cli_module,
        "readiness_verdict",
        lambda: ReadinessVerdict(
            False, "backend readiness has not succeeded yet", _state(failure_code="AUTH_REQUIRED")
        ),
    )

    result = CliRunner().invoke(app, ["health", "--ready"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "UNREADY: backend readiness has not succeeded yet: AUTH_REQUIRED\n"


def test_health_ready_success_emits_ready_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def connect(*_args: object, **_kwargs: object) -> _Connection:
        calls.append("socket")
        return _Connection()

    def verdict() -> ReadinessVerdict:
        calls.append("backend")
        return ReadinessVerdict(True, None, _state())

    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(cli_module, "readiness_verdict", verdict)

    result = CliRunner().invoke(app, ["health", "--ready", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"status": "ready"}
    assert result.stderr == ""
    assert calls == ["socket", "backend"]


@pytest.mark.parametrize("error", [ValueError("malformed readiness state"), OSError("read failed")])
def test_health_ready_classifies_readiness_snapshot_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: _Connection())

    def fail() -> ReadinessVerdict:
        raise error

    monkeypatch.setattr(cli_module, "readiness_verdict", fail)

    result = CliRunner().invoke(app, ["health", "--ready"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.exception is error
