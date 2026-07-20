from dataclasses import dataclass
from pathlib import Path
from typing import Literal

type LogFormat = Literal["text", "json"]
type LogLevelName = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
type McpTransportName = Literal["stdio", "sse", "streamable-http"]
type RepositorySortName = Literal["default", "name", "weakest", "overall", "latest-audit"]

APP_CONFIG_PARENT_DIR_NAME = ".config"
APP_CONFIG_DIR_NAME = "enji-guard"
AUTH_FILE_NAME = "auth.json"
LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "telemetry.jsonl"
STATE_DIR_NAME = "state"
READINESS_STATE_FILE_NAME = "readiness.json"
ACTIVE_RUN_LEDGER_FILE_NAME = "active-runs.json"
AUDIT_CATALOG_STATE_FILE_NAME = "audit-catalog.json"

DEFAULT_BASE_URL = "https://fleet.enji.ai"
DEFAULT_GUARD_ORIGIN = "https://guard.enji.ai"
DEFAULT_GUARD_REFERER = "https://guard.enji.ai/"
DEFAULT_AUTO_REFRESH_ENABLED = True
DEFAULT_AUTO_REFRESH_LEAD_SECONDS = 300
DEFAULT_AUTO_REFRESH_FALLBACK_SECONDS = 900
DEFAULT_AUTO_REFRESH_RETRY_SECONDS = 900
DEFAULT_AUTO_REFRESH_RETRY_INITIAL_SECONDS = 30.0
DEFAULT_AUTO_REFRESH_RETRY_MAX_SECONDS = 3600.0
DEFAULT_AUTO_REFRESH_RETRY_JITTER_SECONDS = 30.0
DEFAULT_TRANSPORT_TIMEOUT_SECONDS = 20.0
DEFAULT_TRANSPORT_RETRY_TOTAL = 3
DEFAULT_TRANSPORT_RETRY_BACKOFF_FACTOR = 0.5
DEFAULT_TRANSPORT_RETRY_MAX_DELAY_SECONDS = 30.0
DEFAULT_TRANSPORT_RETRY_JITTER_SECONDS = 0.5
DEFAULT_TRANSPORT_RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)
DEFAULT_LOG_LEVEL_NAME: LogLevelName = "INFO"
DEFAULT_LOG_FORMAT: LogFormat = "json"
DEFAULT_LOG_MAX_BYTES = 10_000_000
DEFAULT_LOG_BACKUP_COUNT = 5
# The executable's default is a long-lived local HTTP service.  Stdio remains
# available as an explicit transport for an interactive MCP client, but it
# exits normally when stdin closes (which is exactly what happens for
# ``docker run image`` without ``-i``).  Keeping the service default HTTP is
# required for the supervisor/readiness contract and the image healthcheck.
DEFAULT_MCP_TRANSPORT: McpTransportName = "streamable-http"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
DEFAULT_LOCAL_READINESS_TIMEOUT_SECONDS = 2.0
DEFAULT_BACKEND_READINESS_ENABLED = True
DEFAULT_BACKEND_READINESS_INTERVAL_SECONDS = 300
DEFAULT_BACKEND_READINESS_TIMEOUT_SECONDS = 5.0
DEFAULT_BACKEND_READINESS_FAILURE_THRESHOLD = 3
DEFAULT_BACKEND_READINESS_STALE_AFTER_SECONDS = 900
DEFAULT_AUDIT_WAIT_POLL_SECONDS = 30
DEFAULT_AUDIT_WAIT_TIMEOUT_SECONDS = 2700
DEFAULT_AUDIT_WAIT_TIMEOUT_TEXT = "45m"
DEFAULT_AUDIT_WAIT_HEARTBEAT_SECONDS = 120
DEFAULT_REPO_SORT: RepositorySortName = "default"
DEFAULT_ACTIVE_RUN_LEDGER_TTL_SECONDS = 6 * 60 * 60
DEFAULT_ACTIVE_RUN_LOOKUP_GRACE_SECONDS = 300
DEFAULT_FANOUT_MAX_CONCURRENCY = 8


@dataclass(frozen=True, slots=True)
class AuthSettings:
    base_url: str
    auth_file: Path
    guard_origin: str
    guard_referer: str


@dataclass(frozen=True, slots=True)
class AutoRefreshSettings:
    enabled: bool
    lead_seconds: int
    fallback_seconds: int
    retry_seconds: int
    retry_initial_seconds: float = DEFAULT_AUTO_REFRESH_RETRY_INITIAL_SECONDS
    retry_max_seconds: float = DEFAULT_AUTO_REFRESH_RETRY_MAX_SECONDS
    retry_jitter_seconds: float = DEFAULT_AUTO_REFRESH_RETRY_JITTER_SECONDS
    auth_required_retry_seconds: int = DEFAULT_AUTO_REFRESH_RETRY_SECONDS


@dataclass(frozen=True, slots=True)
class TransportRetrySettings:
    total: int
    backoff_factor: float
    max_delay_seconds: float
    jitter_seconds: float
    retryable_status_codes: tuple[int, ...]
    respect_retry_after_header: bool


@dataclass(frozen=True, slots=True)
class TransportSettings:
    timeout_seconds: float
    retry: TransportRetrySettings


@dataclass(frozen=True, slots=True)
class TelemetrySettings:
    level_name: LogLevelName
    log_format: LogFormat
    log_file: Path | None
    max_bytes: int
    backup_count: int


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    local_readiness_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ReadinessSettings:
    enabled: bool
    state_file: Path
    heartbeat_interval_seconds: int
    heartbeat_timeout_seconds: float
    failure_threshold: int
    state_stale_after_seconds: int

    @property
    def ready_after_failure_count(self) -> int:
        return self.failure_threshold - 1


@dataclass(frozen=True, slots=True)
class AuditWaitSettings:
    poll_seconds: int
    timeout_seconds: int
    timeout_text: str
    heartbeat_seconds: int


@dataclass(frozen=True, slots=True)
class RepoSettings:
    default_sort: RepositorySortName


@dataclass(frozen=True, slots=True)
class ActiveRunLedgerSettings:
    state_file: Path
    ttl_seconds: int
    lookup_grace_seconds: int


@dataclass(frozen=True, slots=True)
class AuditCatalogSettings:
    state_file: Path


@dataclass(frozen=True, slots=True)
class FanoutSettings:
    max_concurrency: int


@dataclass(frozen=True, slots=True)
class EnjiGuardSettings:
    auth: AuthSettings
    auto_refresh: AutoRefreshSettings
    transport: TransportSettings
    telemetry: TelemetrySettings
    service: ServiceSettings
    readiness: ReadinessSettings
    audit_wait: AuditWaitSettings
    repo: RepoSettings
    active_run_ledger: ActiveRunLedgerSettings
    audit_catalog: AuditCatalogSettings
    fanout: FanoutSettings


def default_settings() -> EnjiGuardSettings:
    config_root = default_config_root()
    return EnjiGuardSettings(
        auth=AuthSettings(
            base_url=DEFAULT_BASE_URL,
            auth_file=config_root / AUTH_FILE_NAME,
            guard_origin=DEFAULT_GUARD_ORIGIN,
            guard_referer=DEFAULT_GUARD_REFERER,
        ),
        auto_refresh=AutoRefreshSettings(
            enabled=DEFAULT_AUTO_REFRESH_ENABLED,
            lead_seconds=DEFAULT_AUTO_REFRESH_LEAD_SECONDS,
            fallback_seconds=DEFAULT_AUTO_REFRESH_FALLBACK_SECONDS,
            retry_seconds=DEFAULT_AUTO_REFRESH_RETRY_SECONDS,
        ),
        transport=TransportSettings(
            timeout_seconds=DEFAULT_TRANSPORT_TIMEOUT_SECONDS,
            retry=TransportRetrySettings(
                total=DEFAULT_TRANSPORT_RETRY_TOTAL,
                backoff_factor=DEFAULT_TRANSPORT_RETRY_BACKOFF_FACTOR,
                max_delay_seconds=DEFAULT_TRANSPORT_RETRY_MAX_DELAY_SECONDS,
                jitter_seconds=DEFAULT_TRANSPORT_RETRY_JITTER_SECONDS,
                retryable_status_codes=DEFAULT_TRANSPORT_RETRYABLE_STATUS_CODES,
                respect_retry_after_header=True,
            ),
        ),
        telemetry=TelemetrySettings(
            level_name=DEFAULT_LOG_LEVEL_NAME,
            log_format=DEFAULT_LOG_FORMAT,
            log_file=config_root / LOG_DIR_NAME / LOG_FILE_NAME,
            max_bytes=DEFAULT_LOG_MAX_BYTES,
            backup_count=DEFAULT_LOG_BACKUP_COUNT,
        ),
        service=ServiceSettings(
            local_readiness_timeout_seconds=DEFAULT_LOCAL_READINESS_TIMEOUT_SECONDS,
        ),
        readiness=ReadinessSettings(
            enabled=DEFAULT_BACKEND_READINESS_ENABLED,
            state_file=config_root / STATE_DIR_NAME / READINESS_STATE_FILE_NAME,
            heartbeat_interval_seconds=DEFAULT_BACKEND_READINESS_INTERVAL_SECONDS,
            heartbeat_timeout_seconds=DEFAULT_BACKEND_READINESS_TIMEOUT_SECONDS,
            failure_threshold=DEFAULT_BACKEND_READINESS_FAILURE_THRESHOLD,
            state_stale_after_seconds=DEFAULT_BACKEND_READINESS_STALE_AFTER_SECONDS,
        ),
        audit_wait=AuditWaitSettings(
            poll_seconds=DEFAULT_AUDIT_WAIT_POLL_SECONDS,
            timeout_seconds=DEFAULT_AUDIT_WAIT_TIMEOUT_SECONDS,
            timeout_text=DEFAULT_AUDIT_WAIT_TIMEOUT_TEXT,
            heartbeat_seconds=DEFAULT_AUDIT_WAIT_HEARTBEAT_SECONDS,
        ),
        repo=RepoSettings(
            default_sort=DEFAULT_REPO_SORT,
        ),
        active_run_ledger=ActiveRunLedgerSettings(
            state_file=config_root / STATE_DIR_NAME / ACTIVE_RUN_LEDGER_FILE_NAME,
            ttl_seconds=DEFAULT_ACTIVE_RUN_LEDGER_TTL_SECONDS,
            lookup_grace_seconds=DEFAULT_ACTIVE_RUN_LOOKUP_GRACE_SECONDS,
        ),
        audit_catalog=AuditCatalogSettings(
            state_file=config_root / STATE_DIR_NAME / AUDIT_CATALOG_STATE_FILE_NAME,
        ),
        fanout=FanoutSettings(max_concurrency=DEFAULT_FANOUT_MAX_CONCURRENCY),
    )


def default_config_root() -> Path:
    return Path.home() / APP_CONFIG_PARENT_DIR_NAME / APP_CONFIG_DIR_NAME
