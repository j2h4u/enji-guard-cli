import asyncio
import inspect
from collections.abc import Awaitable
from importlib.metadata import version
from pathlib import Path

from enji_guard_cli.audits import AuditAlias, AuditPayload
from enji_guard_cli.audits import audit_catalog as registry_audit_catalog
from enji_guard_cli.audits import audit_payload as registry_audit_payload
from enji_guard_cli.audits import resolve_audit as registry_resolve_audit
from enji_guard_cli.auth import AuthStatusPayload
from enji_guard_cli.auth import auth_status as run_auth_status
from enji_guard_cli.auth import auth_status_async as run_auth_status_async
from enji_guard_cli.core_impl.models import OperationName, OperationPayload, OperationSpec
from enji_guard_cli.enji_api import (
    REPORTS_LIST_DEFAULT_MIN_SEVERITY,
    REPORTS_LIST_DEFAULT_SELECTOR,
    REPORTS_LIST_DEFAULT_STALE,
    AccessPayload,
    ReportsListPayload,
)
from enji_guard_cli.enji_api import access as run_access
from enji_guard_cli.enji_api import access_async as run_access_async
from enji_guard_cli.enji_api import reports_list as run_reports_list
from enji_guard_cli.enji_api import reports_list_async as run_reports_list_async


def _catalog_audits_operation() -> list[AuditPayload]:
    return registry_audit_catalog()


def _catalog_audit_operation(audit: AuditAlias) -> AuditPayload:
    return registry_audit_payload(registry_resolve_audit(audit))


def _access_operation() -> AccessPayload:
    return run_access()


def _reports_list_operation(
    selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    stale: bool = REPORTS_LIST_DEFAULT_STALE,
    min_severity: str | None = REPORTS_LIST_DEFAULT_MIN_SEVERITY,
) -> ReportsListPayload:
    return run_reports_list(selector=selector, stale=stale, min_severity=min_severity)


async def _reports_list_async_operation(
    selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    stale: bool = REPORTS_LIST_DEFAULT_STALE,
    min_severity: str | None = REPORTS_LIST_DEFAULT_MIN_SEVERITY,
) -> ReportsListPayload:
    return await run_reports_list_async(selector=selector, stale=stale, min_severity=min_severity)


def _auth_status_operation(auth_file: Path | None = None) -> AuthStatusPayload:
    return run_auth_status(auth_file)


async def auth_status_async_operation(auth_file: Path | None = None) -> AuthStatusPayload:
    return await run_auth_status_async(auth_file)


OPERATION_SPECS: tuple[OperationSpec, ...] = (
    OperationSpec(
        name=OperationName.CATALOG_AUDITS,
        cli_command="catalog audits",
        mcp_tool="enji_catalog_audits",
        summary="List the canonical Enji Guard audit catalog.",
        execute=_catalog_audits_operation,
    ),
    OperationSpec(
        name=OperationName.CATALOG_AUDIT,
        cli_command="catalog audit",
        mcp_tool="enji_catalog_audit",
        summary="Resolve one canonical Enji Guard audit alias.",
        execute=_catalog_audit_operation,
    ),
    OperationSpec(
        name=OperationName.ACCESS,
        cli_command="access",
        mcp_tool="enji_access",
        summary="Return Enji Guard plan, limits, and schedule access metadata.",
        execute=_access_operation,
    ),
    OperationSpec(
        name=OperationName.REPORTS_LIST,
        cli_command=None,
        mcp_tool="enji_reports_list",
        summary="List compact Enji Guard report inventory for MCP.",
        execute=_reports_list_operation,
    ),
    OperationSpec(
        name=OperationName.AUTH_STATUS,
        cli_command="auth status",
        mcp_tool="enji_auth_status",
        summary="Report whether stored Enji Guard credentials are authenticated.",
        execute=_auth_status_operation,
    ),
)

_OPERATION_BY_NAME: dict[OperationName, OperationSpec] = {spec.name: spec for spec in OPERATION_SPECS}


def package_version() -> str:
    return version("enji-guard-cli")


async def _await_operation_result[T](result: Awaitable[T]) -> T:
    return await result


def resolve_operation_result[T](result: T | Awaitable[T]) -> T:
    if inspect.isawaitable(result):
        return asyncio.run(_await_operation_result(result))
    return result


async def access_async_operation() -> AccessPayload:
    return await run_access_async()


async def reports_list_async_operation(
    selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    stale: bool = REPORTS_LIST_DEFAULT_STALE,
    min_severity: str | None = REPORTS_LIST_DEFAULT_MIN_SEVERITY,
) -> ReportsListPayload:
    return await _reports_list_async_operation(selector=selector, stale=stale, min_severity=min_severity)


def operation_payload(spec: OperationSpec) -> OperationPayload:
    return {
        "name": spec.name.value,
        "cli_command": spec.cli_command,
        "mcp_tool": spec.mcp_tool,
        "summary": spec.summary,
    }


def operation_catalog() -> list[OperationPayload]:
    return [operation_payload(spec) for spec in OPERATION_SPECS]


def resolve_operation_spec(name: OperationName) -> OperationSpec:
    spec = _OPERATION_BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"unknown operation name: {name}")
    return spec


def resolve_operation(name: OperationName) -> OperationPayload:
    return operation_payload(resolve_operation_spec(name))
