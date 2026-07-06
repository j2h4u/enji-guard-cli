import json
import logging
import stat
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from pytest import MonkeyPatch

from enji_guard_cli.settings import LogFormat, LogLevelName, TelemetrySettings
from enji_guard_cli.telemetry import configure_logging, log_event


def test_json_logging_keeps_structured_safe_fields_and_drops_objects(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(_telemetry_settings(log_file=None, log_format="json"))
    logger = logging.getLogger("enji_guard_cli.test")

    log_event(
        logger,
        logging.INFO,
        "event_name",
        {
            "operation": "access",
            "elapsed_ms": 12,
            "ignored_object": object(),
        },
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = cast(object, json.loads(captured.err))
    assert isinstance(payload, dict)
    timestamp = payload.pop("timestamp")
    assert isinstance(timestamp, str)
    datetime.fromisoformat(timestamp)

    assert payload == {
        "elapsed_ms": 12,
        "level": "info",
        "logger": "enji_guard_cli.test",
        "message": "event_name",
        "operation": "access",
        "provenance": "test",
    }


def test_configure_logging_can_write_json_lines_to_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_file = tmp_path / "logs" / "telemetry.jsonl"
    configure_logging(_telemetry_settings(log_file=log_file, log_format="json"))
    logger = logging.getLogger("enji_guard_cli.test")

    log_event(logger, logging.INFO, "event_name", {"operation": "wait", "elapsed_ms": 12})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    payload = cast(object, json.loads(log_file.read_text(encoding="utf-8")))
    assert isinstance(payload, dict)
    timestamp = payload.pop("timestamp")
    assert isinstance(timestamp, str)
    datetime.fromisoformat(timestamp)
    assert payload == {
        "elapsed_ms": 12,
        "level": "info",
        "logger": "enji_guard_cli.test",
        "message": "event_name",
        "operation": "wait",
        "provenance": "test",
    }
    assert stat.S_IMODE(log_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600


def test_configure_logging_tightens_preexisting_permissive_log_directory(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(mode=0o777)
    log_file = log_dir / "telemetry.jsonl"

    configure_logging(_telemetry_settings(log_file=log_file, log_format="json"))
    logger = logging.getLogger("enji_guard_cli.test")
    log_event(logger, logging.INFO, "event_name", {"operation": "wait"})

    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600


def test_default_test_logging_is_noop(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    configure_logging()
    logger = logging.getLogger("enji_guard_cli.test")

    log_event(logger, logging.INFO, "event_name", {"operation": "wait"})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert not (tmp_path / ".config" / "enji-guard" / "logs" / "telemetry.jsonl").exists()


def test_default_test_logging_is_noop_for_explicit_provenance(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    configure_logging(provenance="cli")
    logger = logging.getLogger("enji_guard_cli.test")

    log_event(logger, logging.INFO, "event_name", {"operation": "wait"})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert not (tmp_path / ".config" / "enji-guard" / "logs" / "telemetry.jsonl").exists()


def test_configure_logging_allows_explicit_provenance(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(_telemetry_settings(log_file=None, log_format="json"), provenance="mcp")
    logger = logging.getLogger("enji_guard_cli.test")

    log_event(logger, logging.INFO, "event_name", {"operation": "tool"})

    payload = cast(object, json.loads(capsys.readouterr().err))
    assert isinstance(payload, dict)
    assert payload["provenance"] == "mcp"


def test_configure_logging_preserves_jsonl_rotation(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "telemetry.jsonl"
    configure_logging(
        TelemetrySettings(
            level_name="INFO",
            log_format="json",
            log_file=log_file,
            max_bytes=160,
            backup_count=1,
        )
    )
    logger = logging.getLogger("enji_guard_cli.test")

    log_event(logger, logging.INFO, "event_name", {"operation": "first", "payload": "x" * 80})
    log_event(logger, logging.INFO, "event_name", {"operation": "second", "payload": "y" * 80})

    assert log_file.exists()
    assert log_file.with_suffix(".jsonl.1").exists()


def test_configure_logging_keeps_httpx_quiet_by_default() -> None:
    configure_logging(_telemetry_settings(log_file=None, log_format="text"))

    assert logging.getLogger("httpx").level == logging.WARNING


def _telemetry_settings(
    *,
    log_file: Path | None,
    log_format: LogFormat,
    level_name: LogLevelName = "INFO",
) -> TelemetrySettings:
    return TelemetrySettings(
        level_name=level_name,
        log_format=log_format,
        log_file=log_file,
        max_bytes=10_000,
        backup_count=1,
    )
