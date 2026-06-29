import json
import logging
import os
import sys
from collections.abc import Mapping
from typing import Literal, TypeGuard

LOG_LEVEL_ENV = "ENJI_GUARD_LOG_LEVEL"
LOG_FORMAT_ENV = "ENJI_GUARD_LOG_FORMAT"
DEFAULT_LOG_LEVEL = "WARNING"

type LogFormat = Literal["text", "json"]
type LogFieldValue = None | bool | int | float | str


class EnjiJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, LogFieldValue] = {
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        raw_fields = getattr(record, "enji_fields", None)
        if isinstance(raw_fields, Mapping):
            payload.update(_safe_log_fields(raw_fields))
        return json.dumps(payload, sort_keys=True)


def configure_logging(level: str | None = None, log_format: LogFormat | None = None) -> None:
    logger = logging.getLogger("enji_guard_cli")
    logger.handlers.clear()
    logger.setLevel(_parse_log_level(level or os.environ.get(LOG_LEVEL_ENV, DEFAULT_LOG_LEVEL)))
    logger.propagate = False

    handler = logging.StreamHandler(sys.stderr)
    if _resolve_log_format(log_format) == "json":
        handler.setFormatter(EnjiJsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    fields: Mapping[str, object],
) -> None:
    if logger.isEnabledFor(level):
        logger.log(level, event, extra={"enji_fields": fields})


def _parse_log_level(raw_level: str) -> int:
    levels = {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
    }
    return levels.get(raw_level.strip().upper(), logging.WARNING)


def _resolve_log_format(log_format: LogFormat | None) -> LogFormat:
    if log_format is not None:
        return log_format
    return "json" if os.environ.get(LOG_FORMAT_ENV, "").strip().lower() == "json" else "text"


def _safe_log_fields(fields: Mapping[object, object]) -> dict[str, LogFieldValue]:
    return {key: value for key, value in fields.items() if isinstance(key, str) and _is_log_field_value(value)}


def _is_log_field_value(value: object) -> TypeGuard[LogFieldValue]:
    return value is None or isinstance(value, (bool, int, float, str))
