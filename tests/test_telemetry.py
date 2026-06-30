import json
import logging
from pathlib import Path
from typing import cast

import pytest

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

    assert payload == {
        "elapsed_ms": 12,
        "level": "info",
        "logger": "enji_guard_cli.test",
        "message": "event_name",
        "operation": "access",
    }


def test_configure_logging_can_write_json_lines_to_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_file = tmp_path / "logs" / "enji-guard.jsonl"
    configure_logging(_telemetry_settings(log_file=log_file, log_format="json"))
    logger = logging.getLogger("enji_guard_cli.test")

    log_event(logger, logging.INFO, "event_name", {"operation": "wait", "elapsed_ms": 12})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    payload = cast(object, json.loads(log_file.read_text(encoding="utf-8")))
    assert isinstance(payload, dict)
    assert payload == {
        "elapsed_ms": 12,
        "level": "info",
        "logger": "enji_guard_cli.test",
        "message": "event_name",
        "operation": "wait",
    }


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
