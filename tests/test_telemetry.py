import json
import logging
from typing import cast

import pytest

from enji_guard_cli.telemetry import configure_logging, log_event


def test_json_logging_keeps_structured_safe_fields_and_drops_objects(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
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


def test_configure_logging_keeps_httpx_quiet_by_default() -> None:
    configure_logging("INFO", "text")

    assert logging.getLogger("httpx").level == logging.WARNING
