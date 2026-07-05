import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from enji_guard_cli.telemetry import log_event, telemetry_provenance

type JourneyBody = Callable[[], object]
type ExitCodeResolver = Callable[[Exception], int]

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentJourney:
    event_prefix: str
    operation: str
    surface: str
    provenance: str | None = None
    json_output: bool = False
    selector_kind: str = "unknown"
    all_flag: bool | None = None


def run_agent_journey(
    body: JourneyBody,
    journey: AgentJourney,
    *,
    exit_code_for_exception: ExitCodeResolver | None = None,
) -> object:
    record = _StartedJourney(journey=journey, started_at=monotonic())
    result: object | None = None
    with telemetry_provenance(journey.provenance):
        log_event(_LOGGER, logging.INFO, f"{journey.event_prefix}_started", _start_fields(journey))
        try:
            result = body()
        except Exception as exc:
            record.exit_code = _exit_code(exc, exit_code_for_exception)
            raise
        else:
            return result
        finally:
            _log_finished(record, result)


@dataclass(slots=True)
class _StartedJourney:
    journey: AgentJourney
    started_at: float
    exit_code: int = 0


def _start_fields(journey: AgentJourney) -> dict[str, object]:
    fields: dict[str, object] = {
        "operation": journey.operation,
        "surface": journey.surface,
        "json": journey.json_output,
        "selector_kind": journey.selector_kind,
    }
    if journey.surface == "cli":
        fields["command_path"] = journey.operation
    if journey.surface == "mcp":
        fields["tool_name"] = journey.operation
    if journey.all_flag is not None:
        fields["all"] = journey.all_flag
    return fields


def _log_finished(record: _StartedJourney, result: object | None) -> None:
    fields = {
        **_start_fields(record.journey),
        "duration_ms": int((monotonic() - record.started_at) * 1000),
        "exit_code": record.exit_code,
    }
    result_count = _result_count(result)
    if result_count is not None:
        fields["result_count"] = result_count
    log_event(_LOGGER, logging.INFO, f"{record.journey.event_prefix}_finished", fields)


def _exit_code(exc: Exception, resolver: ExitCodeResolver | None) -> int:
    if resolver is None:
        return 1
    return resolver(exc)


def _result_count(result: object | None) -> int | None:
    if isinstance(result, dict):
        for key in ("projects", "reports", "results", "items", "schedules", "preferences", "audits"):
            value = result.get(key)
            if isinstance(value, list):
                return len(value)
    if isinstance(result, list):
        return len(result)
    return None
