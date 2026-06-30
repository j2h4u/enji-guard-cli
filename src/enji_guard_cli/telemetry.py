import json
import logging
import sys
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from typing import TypeGuard

from enji_guard_cli.settings import TelemetrySettings, default_settings

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


def configure_logging(settings: TelemetrySettings | None = None) -> None:
    telemetry_settings = settings if settings is not None else default_settings().telemetry
    logger = logging.getLogger("enji_guard_cli")
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.setLevel(_parse_log_level(telemetry_settings.level_name))
    logger.propagate = False

    handler = _build_handler(telemetry_settings)
    if telemetry_settings.log_format == "json":
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


def _build_handler(settings: TelemetrySettings) -> logging.Handler:
    if settings.log_file is None:
        return logging.StreamHandler(sys.stderr)
    path = settings.log_file.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return RotatingFileHandler(
        path,
        maxBytes=settings.max_bytes,
        backupCount=settings.backup_count,
        encoding="utf-8",
        delay=True,
    )


def _safe_log_fields(fields: Mapping[object, object]) -> dict[str, LogFieldValue]:
    return {key: value for key, value in fields.items() if isinstance(key, str) and _is_log_field_value(value)}


def _is_log_field_value(value: object) -> TypeGuard[LogFieldValue]:
    return value is None or isinstance(value, (bool, int, float, str))
