from dataclasses import dataclass
from pathlib import Path
from typing import Literal

type LogFormat = Literal["text", "json"]
type LogLevelName = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
type McpTransportName = Literal["stdio", "sse", "streamable-http"]

APP_CONFIG_PARENT_DIR_NAME = ".config"
APP_CONFIG_DIR_NAME = "enji-guard"
AUTH_FILE_NAME = "auth.json"
LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "telemetry.jsonl"

DEFAULT_BASE_URL = "https://fleet.enji.ai"
DEFAULT_AUTO_REFRESH_ENABLED = True
DEFAULT_AUTO_REFRESH_LEAD_SECONDS = 300
DEFAULT_AUTO_REFRESH_FALLBACK_SECONDS = 900
DEFAULT_AUTO_REFRESH_RETRY_SECONDS = 60
DEFAULT_LOG_LEVEL_NAME: LogLevelName = "INFO"
DEFAULT_LOG_FORMAT: LogFormat = "json"
DEFAULT_LOG_MAX_BYTES = 10_000_000
DEFAULT_LOG_BACKUP_COUNT = 5
DEFAULT_MCP_TRANSPORT: McpTransportName = "stdio"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000


@dataclass(frozen=True, slots=True)
class AuthSettings:
    base_url: str
    auth_file: Path


@dataclass(frozen=True, slots=True)
class AutoRefreshSettings:
    enabled: bool
    lead_seconds: int
    fallback_seconds: int
    retry_seconds: int


@dataclass(frozen=True, slots=True)
class TelemetrySettings:
    level_name: LogLevelName
    log_format: LogFormat
    log_file: Path | None
    max_bytes: int
    backup_count: int


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    mcp_transport: McpTransportName
    http_host: str
    http_port: int


@dataclass(frozen=True, slots=True)
class EnjiGuardSettings:
    auth: AuthSettings
    auto_refresh: AutoRefreshSettings
    telemetry: TelemetrySettings
    service: ServiceSettings


def default_settings() -> EnjiGuardSettings:
    config_root = default_config_root()
    return EnjiGuardSettings(
        auth=AuthSettings(base_url=DEFAULT_BASE_URL, auth_file=config_root / AUTH_FILE_NAME),
        auto_refresh=AutoRefreshSettings(
            enabled=DEFAULT_AUTO_REFRESH_ENABLED,
            lead_seconds=DEFAULT_AUTO_REFRESH_LEAD_SECONDS,
            fallback_seconds=DEFAULT_AUTO_REFRESH_FALLBACK_SECONDS,
            retry_seconds=DEFAULT_AUTO_REFRESH_RETRY_SECONDS,
        ),
        telemetry=TelemetrySettings(
            level_name=DEFAULT_LOG_LEVEL_NAME,
            log_format=DEFAULT_LOG_FORMAT,
            log_file=config_root / LOG_DIR_NAME / LOG_FILE_NAME,
            max_bytes=DEFAULT_LOG_MAX_BYTES,
            backup_count=DEFAULT_LOG_BACKUP_COUNT,
        ),
        service=ServiceSettings(
            mcp_transport=DEFAULT_MCP_TRANSPORT,
            http_host=DEFAULT_HTTP_HOST,
            http_port=DEFAULT_HTTP_PORT,
        ),
    )


def default_config_root() -> Path:
    return Path.home() / APP_CONFIG_PARENT_DIR_NAME / APP_CONFIG_DIR_NAME
