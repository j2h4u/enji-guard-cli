import json
import logging
import os
import sys
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal, TypeGuard

LOG_LEVEL_ENV = "ENJI_GUARD_LOG_LEVEL"
LOG_FORMAT_ENV = "ENJI_GUARD_LOG_FORMAT"
LOG_FILE_ENV = "ENJI_GUARD_LOG_FILE"
LOG_MAX_BYTES_ENV = "ENJI_GUARD_LOG_MAX_BYTES"
LOG_BACKUP_COUNT_ENV = "ENJI_GUARD_LOG_BACKUP_COUNT"
DEFAULT_LOG_LEVEL = "WARNING"
DEFAULT_LOG_MAX_BYTES = 10_000_000
DEFAULT_LOG_BACKUP_COUNT = 5

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


def configure_logging(
    level: str | None = None,
    log_format: LogFormat | None = None,
    log_file: str | None = None,
) -> None:
    logger = logging.getLogger("enji_guard_cli")
    logger.handlers.clear()
    logger.setLevel(_parse_log_level(level or os.environ.get(LOG_LEVEL_ENV, DEFAULT_LOG_LEVEL)))
    logger.propagate = False

    handler = _build_handler(log_file)
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


def _build_handler(log_file: str | None) -> logging.Handler:
    path = _resolve_log_file(log_file)
    if path is None:
        return logging.StreamHandler(sys.stderr)
    path.parent.mkdir(parents=True, exist_ok=True)
    return RotatingFileHandler(
        path,
        maxBytes=_env_int(LOG_MAX_BYTES_ENV, DEFAULT_LOG_MAX_BYTES),
        backupCount=_env_int(LOG_BACKUP_COUNT_ENV, DEFAULT_LOG_BACKUP_COUNT),
        encoding="utf-8",
    )


def _resolve_log_file(log_file: str | None) -> Path | None:
    raw_value = log_file if log_file is not None else os.environ.get(LOG_FILE_ENV)
    if raw_value is None:
        return None
    value = raw_value.strip()
    if not value or value == "-":
        return None
    return Path(value).expanduser()


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value >= 0 else default


def _safe_log_fields(fields: Mapping[object, object]) -> dict[str, LogFieldValue]:
    return {key: value for key, value in fields.items() if isinstance(key, str) and _is_log_field_value(value)}


def _is_log_field_value(value: object) -> TypeGuard[LogFieldValue]:
    return value is None or isinstance(value, (bool, int, float, str))
