import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from logging import INFO, Formatter, LogRecord
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Protocol

type LogFieldValue = None | bool | int | float | str


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    timestamp: datetime
    level: str
    logger: str
    message: str
    fields: dict[str, LogFieldValue]

    def to_jsonl_payload(self) -> dict[str, LogFieldValue]:
        payload: dict[str, LogFieldValue] = {
            "timestamp": self.timestamp.astimezone(UTC).isoformat(),
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }
        payload.update(self.fields)
        return payload


class TelemetrySink(Protocol):
    def emit(self, event: TelemetryEvent) -> None: ...

    def close(self) -> None: ...


class NoopTelemetrySink:
    def emit(self, event: TelemetryEvent) -> None:
        _ = event

    def close(self) -> None:
        return None


@dataclass(slots=True)
class _TextStreamSink:
    formatter: Callable[[TelemetryEvent], str]
    lock: Lock

    def emit(self, event: TelemetryEvent) -> None:
        line = self.formatter(event)
        with self.lock:
            sys.stderr.write(f"{line}\n")
            sys.stderr.flush()

    def close(self) -> None:
        return None


@dataclass(slots=True)
class _FileJsonlSink:
    handler: RotatingFileHandler

    def emit(self, event: TelemetryEvent) -> None:
        line = json.dumps(event.to_jsonl_payload(), sort_keys=True)
        self.handler.emit(
            LogRecord(
                name="enji_guard_cli.telemetry_sink",
                level=INFO,
                pathname="",
                lineno=0,
                msg=line,
                args=(),
                exc_info=None,
            )
        )

    def close(self) -> None:
        self.handler.close()


def build_telemetry_sink(
    *,
    log_file: Path | None,
    json_format: bool,
    max_bytes: int,
    backup_count: int,
    text_formatter: Callable[[TelemetryEvent], str],
) -> TelemetrySink:
    if log_file is None:
        formatter = _jsonl_line if json_format else text_formatter
        return _TextStreamSink(formatter=formatter, lock=Lock())
    path = log_file.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(Formatter("%(message)s"))
    return _FileJsonlSink(handler=handler)


def _jsonl_line(event: TelemetryEvent) -> str:
    return json.dumps(event.to_jsonl_payload(), sort_keys=True)
