from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

type LogFormat = Literal["text", "json"]
type LogLevelName = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
type McpTransportName = Literal["stdio", "sse", "streamable-http"]
type RepositorySortName = Literal["default", "name", "weakest", "overall", "latest-audit"]

LOG_FORMAT_NAMES = frozenset({"text", "json"})
LOG_LEVEL_NAMES = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})
REPOSITORY_SORT_NAMES = frozenset({"default", "name", "weakest", "overall", "latest-audit"})
MIN_HTTP_STATUS_CODE = 100
MAX_HTTP_STATUS_CODE = 599

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
# Bind-mounted filesystems do not reliably deliver inotify events into the
# container.  Polling this revision is therefore the correctness mechanism;
# the watcher only shortens normal wake-up latency.
DEFAULT_AUTO_REFRESH_REVISION_POLL_SECONDS = 5.0
DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_LIMIT = 3
DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_INITIAL_SECONDS = 1.0
DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_MAX_SECONDS = 10.0
DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_JITTER_SECONDS = 0.25
DEFAULT_TRANSPORT_TIMEOUT_SECONDS = 20.0
DEFAULT_TRANSPORT_RETRY_TOTAL = 3
DEFAULT_TRANSPORT_RETRY_BACKOFF_FACTOR = 0.5
DEFAULT_TRANSPORT_RETRY_MAX_DELAY_SECONDS = 30.0
DEFAULT_TRANSPORT_RETRY_JITTER_SECONDS = 0.5
DEFAULT_TRANSPORT_RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)
DEFAULT_TRANSPORT_MAX_CONNECTIONS = 20
DEFAULT_TRANSPORT_MAX_KEEPALIVE_CONNECTIONS = 20
DEFAULT_TRANSPORT_KEEPALIVE_EXPIRY_SECONDS = 5.0
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
DEFAULT_MCP_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 5.0
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

    def __post_init__(self) -> None:
        _require_non_empty("auth.base_url", self.base_url)
        _require_non_empty("auth.guard_origin", self.guard_origin)
        _require_non_empty("auth.guard_referer", self.guard_referer)


@dataclass(frozen=True, slots=True)
class AutoRefreshSettings:
    enabled: bool
    lead_seconds: int
    fallback_seconds: int
    revision_poll_seconds: float = DEFAULT_AUTO_REFRESH_REVISION_POLL_SECONDS
    pre_dispatch_retry_limit: int = DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_LIMIT
    pre_dispatch_retry_initial_seconds: float = DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_INITIAL_SECONDS
    pre_dispatch_retry_max_seconds: float = DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_MAX_SECONDS
    pre_dispatch_retry_jitter_seconds: float = DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_JITTER_SECONDS

    def __post_init__(self) -> None:
        _require_non_negative("auto_refresh.lead_seconds", self.lead_seconds)
        _require_positive("auto_refresh.fallback_seconds", self.fallback_seconds)
        _require_positive("auto_refresh.revision_poll_seconds", self.revision_poll_seconds)
        _require_non_negative("auto_refresh.pre_dispatch_retry_limit", self.pre_dispatch_retry_limit)
        _require_positive("auto_refresh.pre_dispatch_retry_initial_seconds", self.pre_dispatch_retry_initial_seconds)
        _require_positive("auto_refresh.pre_dispatch_retry_max_seconds", self.pre_dispatch_retry_max_seconds)
        _require_non_negative("auto_refresh.pre_dispatch_retry_jitter_seconds", self.pre_dispatch_retry_jitter_seconds)
        if self.pre_dispatch_retry_initial_seconds > self.pre_dispatch_retry_max_seconds:
            raise ValueError("auto_refresh.pre_dispatch_retry_initial_seconds must be <= max seconds")


@dataclass(frozen=True, slots=True)
class TransportRetrySettings:
    total: int
    backoff_factor: float
    max_delay_seconds: float
    jitter_seconds: float
    retryable_status_codes: tuple[int, ...]
    respect_retry_after_header: bool

    def __post_init__(self) -> None:
        _require_non_negative("transport.retry.total", self.total)
        _require_non_negative("transport.retry.backoff_factor", self.backoff_factor)
        _require_positive("transport.retry.max_delay_seconds", self.max_delay_seconds)
        _require_non_negative("transport.retry.jitter_seconds", self.jitter_seconds)
        if not self.retryable_status_codes:
            raise ValueError("transport.retry.retryable_status_codes must not be empty")
        for status_code in self.retryable_status_codes:
            if status_code < MIN_HTTP_STATUS_CODE or status_code > MAX_HTTP_STATUS_CODE:
                raise ValueError("transport.retry.retryable_status_codes must be valid HTTP status codes")


@dataclass(frozen=True, slots=True)
class TransportPoolSettings:
    max_connections: int = DEFAULT_TRANSPORT_MAX_CONNECTIONS
    max_keepalive_connections: int = DEFAULT_TRANSPORT_MAX_KEEPALIVE_CONNECTIONS
    keepalive_expiry_seconds: float = DEFAULT_TRANSPORT_KEEPALIVE_EXPIRY_SECONDS

    def __post_init__(self) -> None:
        _require_positive("transport.pool.max_connections", self.max_connections)
        _require_non_negative("transport.pool.max_keepalive_connections", self.max_keepalive_connections)
        _require_positive("transport.pool.keepalive_expiry_seconds", self.keepalive_expiry_seconds)
        if self.max_keepalive_connections > self.max_connections:
            raise ValueError("transport.pool.max_keepalive_connections must be <= max_connections")


@dataclass(frozen=True, slots=True)
class TransportSettings:
    timeout_seconds: float
    retry: TransportRetrySettings
    pool: TransportPoolSettings = field(default_factory=TransportPoolSettings)

    def __post_init__(self) -> None:
        _require_positive("transport.timeout_seconds", self.timeout_seconds)


@dataclass(frozen=True, slots=True)
class TelemetrySettings:
    level_name: LogLevelName
    log_format: LogFormat
    log_file: Path | None
    max_bytes: int
    backup_count: int

    def __post_init__(self) -> None:
        _require_one_of("telemetry.level_name", self.level_name, LOG_LEVEL_NAMES)
        _require_one_of("telemetry.log_format", self.log_format, LOG_FORMAT_NAMES)
        _require_positive("telemetry.max_bytes", self.max_bytes)
        _require_non_negative("telemetry.backup_count", self.backup_count)


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    local_readiness_timeout_seconds: float
    mcp_graceful_shutdown_timeout_seconds: float = DEFAULT_MCP_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        _require_positive("service.local_readiness_timeout_seconds", self.local_readiness_timeout_seconds)
        _require_positive("service.mcp_graceful_shutdown_timeout_seconds", self.mcp_graceful_shutdown_timeout_seconds)


@dataclass(frozen=True, slots=True)
class ReadinessSettings:
    enabled: bool
    state_file: Path
    heartbeat_interval_seconds: int
    heartbeat_timeout_seconds: float
    failure_threshold: int
    state_stale_after_seconds: int

    def __post_init__(self) -> None:
        _require_positive("readiness.heartbeat_interval_seconds", self.heartbeat_interval_seconds)
        _require_positive("readiness.heartbeat_timeout_seconds", self.heartbeat_timeout_seconds)
        _require_positive("readiness.failure_threshold", self.failure_threshold)
        _require_positive("readiness.state_stale_after_seconds", self.state_stale_after_seconds)

    @property
    def ready_after_failure_count(self) -> int:
        return self.failure_threshold - 1


@dataclass(frozen=True, slots=True)
class AuditWaitSettings:
    poll_seconds: int
    timeout_seconds: int
    timeout_text: str
    heartbeat_seconds: int

    def __post_init__(self) -> None:
        _require_positive("audit_wait.poll_seconds", self.poll_seconds)
        _require_positive("audit_wait.timeout_seconds", self.timeout_seconds)
        _require_non_empty("audit_wait.timeout_text", self.timeout_text)
        _require_positive("audit_wait.heartbeat_seconds", self.heartbeat_seconds)
        if self.timeout_seconds < self.poll_seconds:
            raise ValueError("audit_wait.timeout_seconds must be >= poll_seconds")


@dataclass(frozen=True, slots=True)
class RepoSettings:
    default_sort: RepositorySortName

    def __post_init__(self) -> None:
        _require_one_of("repo.default_sort", self.default_sort, REPOSITORY_SORT_NAMES)


@dataclass(frozen=True, slots=True)
class ActiveRunLedgerSettings:
    state_file: Path
    ttl_seconds: int
    lookup_grace_seconds: int

    def __post_init__(self) -> None:
        _require_positive("active_run_ledger.ttl_seconds", self.ttl_seconds)
        _require_non_negative("active_run_ledger.lookup_grace_seconds", self.lookup_grace_seconds)


@dataclass(frozen=True, slots=True)
class AuditCatalogSettings:
    state_file: Path


@dataclass(frozen=True, slots=True)
class FanoutSettings:
    max_concurrency: int

    def __post_init__(self) -> None:
        _require_positive("fanout.max_concurrency", self.max_concurrency)


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
            revision_poll_seconds=DEFAULT_AUTO_REFRESH_REVISION_POLL_SECONDS,
            pre_dispatch_retry_limit=DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_LIMIT,
            pre_dispatch_retry_initial_seconds=DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_INITIAL_SECONDS,
            pre_dispatch_retry_max_seconds=DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_MAX_SECONDS,
            pre_dispatch_retry_jitter_seconds=DEFAULT_AUTO_REFRESH_PRE_DISPATCH_RETRY_JITTER_SECONDS,
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
            pool=TransportPoolSettings(),
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
            mcp_graceful_shutdown_timeout_seconds=DEFAULT_MCP_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
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


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_positive(name: str, value: int | float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _require_non_negative(name: str, value: int | float) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _require_one_of(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {allowed_values}")
