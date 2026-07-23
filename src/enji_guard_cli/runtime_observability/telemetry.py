import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TypeGuard

from enji_guard_cli.runtime_observability.telemetry_sink import (
    LogFieldValue,
    NoopTelemetrySink,
    TelemetryEvent,
    TelemetrySink,
    build_telemetry_sink,
)
from enji_guard_cli.settings import TelemetrySettings, default_settings

_ACTIVE_SINK: TelemetrySink | None = None
_ACTIVE_PROVENANCE = "runtime"
_CONTEXT_PROVENANCE: ContextVar[str | None] = ContextVar("enji_guard_telemetry_provenance", default=None)


def configure_logging(settings: TelemetrySettings | None = None, *, provenance: str | None = None) -> None:
    global _ACTIVE_SINK
    global _ACTIVE_PROVENANCE
    if _ACTIVE_SINK is not None:
        _ACTIVE_SINK.close()
    _ACTIVE_PROVENANCE = provenance or _default_provenance()
    telemetry_settings = settings if settings is not None else default_settings().telemetry
    _ACTIVE_SINK = (
        NoopTelemetrySink()
        if settings is None and _running_under_pytest()
        else build_telemetry_sink(
            log_file=telemetry_settings.log_file,
            json_format=telemetry_settings.log_format == "json",
            max_bytes=telemetry_settings.max_bytes,
            backup_count=telemetry_settings.backup_count,
            text_formatter=lambda event: f"{event.level.upper()} {event.logger}: {event.message}",
        )
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
    persist_event(logger, level, event, fields)


def persist_event(
    logger: logging.Logger,
    level: int,
    event: str,
    fields: Mapping[str, object],
) -> bool:
    """Persist one telemetry event and report durable sink acceptance.

    Rotation journals use this result as their outbox acknowledgement.  An
    unavailable sink, filtered logger, or sink exception intentionally leaves
    that journal in place for a later supervisor reconciliation.
    """
    if logger.isEnabledFor(level):
        sink = _ACTIVE_SINK
        if sink is None:
            return False
        try:
            sink.emit(
                TelemetryEvent(
                    timestamp=datetime.now(UTC),
                    level=logging.getLevelName(level).lower(),
                    logger=logger.name,
                    message=event,
                    provenance=_event_provenance(),
                    fields=dict(_safe_log_fields(fields)),
                )
            )
        except OSError, RuntimeError:
            return False
        return True
    return False


@contextmanager
def telemetry_provenance(provenance: str | None) -> Iterator[None]:
    if provenance is None:
        yield
        return
    token = _CONTEXT_PROVENANCE.set(provenance)
    try:
        yield
    finally:
        _CONTEXT_PROVENANCE.reset(token)


def _event_provenance() -> str:
    return _CONTEXT_PROVENANCE.get() or _ACTIVE_PROVENANCE


def _default_provenance() -> str:
    if _running_under_pytest():
        return "test"
    return "runtime"


def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def _parse_log_level(raw_level: str) -> int:
    levels = {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
    }
    level_name = raw_level.strip().upper()
    if level_name not in levels:
        raise ValueError(f"unknown telemetry log level: {raw_level}")
    return levels[level_name]


def _safe_log_fields(fields: Mapping[str, object]) -> dict[str, LogFieldValue]:
    return {key: value for key, value in fields.items() if _is_log_field_value(value)}


def _is_log_field_value(value: object) -> TypeGuard[LogFieldValue]:
    return value is None or isinstance(value, (bool, int, float, str))
