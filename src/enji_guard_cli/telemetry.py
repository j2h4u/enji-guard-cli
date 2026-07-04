import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TypeGuard

from enji_guard_cli.settings import TelemetrySettings, default_settings
from enji_guard_cli.telemetry_sink import (
    LogFieldValue,
    TelemetryEvent,
    TelemetrySink,
    build_telemetry_sink,
)

_ACTIVE_SINK: TelemetrySink | None = None


def configure_logging(settings: TelemetrySettings | None = None) -> None:
    telemetry_settings = settings if settings is not None else default_settings().telemetry
    global _ACTIVE_SINK
    if _ACTIVE_SINK is not None:
        _ACTIVE_SINK.close()
    _ACTIVE_SINK = build_telemetry_sink(
        log_file=telemetry_settings.log_file,
        json_format=telemetry_settings.log_format == "json",
        max_bytes=telemetry_settings.max_bytes,
        backup_count=telemetry_settings.backup_count,
        text_formatter=lambda event: f"{event.level.upper()} {event.logger}: {event.message}",
    )
    logger = logging.getLogger("enji_guard_cli")
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.setLevel(_parse_log_level(telemetry_settings.level_name))
    logger.propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    fields: Mapping[str, object],
) -> None:
    if logger.isEnabledFor(level):
        sink = _ACTIVE_SINK
        if sink is None:
            return
        sink.emit(
            TelemetryEvent(
                timestamp=datetime.now(UTC),
                level=logging.getLevelName(level).lower(),
                logger=logger.name,
                message=event,
                fields=dict(_safe_log_fields(fields)),
            )
        )


def _parse_log_level(raw_level: str) -> int:
    levels = {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
    }
    return levels.get(raw_level.strip().upper(), logging.WARNING)


def _safe_log_fields(fields: Mapping[str, object]) -> dict[str, LogFieldValue]:
    return {key: value for key, value in fields.items() if _is_log_field_value(value)}


def _is_log_field_value(value: object) -> TypeGuard[LogFieldValue]:
    return value is None or isinstance(value, (bool, int, float, str))
