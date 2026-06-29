import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import TypedDict

from enji_guard_cli.auth import AuthStatusPayload
from enji_guard_cli.auth import auth_status as run_auth_status
from enji_guard_cli.enji_api import (
    REPORTS_LIST_DEFAULT_MIN_SEVERITY,
    REPORTS_LIST_DEFAULT_SELECTOR,
    REPORTS_LIST_DEFAULT_STALE,
    AccessPayload,
    ReportsListPayload,
)
from enji_guard_cli.enji_api import access as run_access
from enji_guard_cli.enji_api import reports_list as run_reports_list

type OperationResult = object | Awaitable[object]
type OperationExecutor = Callable[..., OperationResult]


class AuditAlias(StrEnum):
    SECURITY = "security"
    AI_READINESS = "ai-readiness"
    TESTS = "tests"
    TECH_HEALTH = "tech-health"
    DEPS = "deps"
    DEAD_CODE = "dead-code"
    RECON = "recon"


class AuditPayload(TypedDict):
    alias: str
    label: str
    route_slug: str | None
    job_kind: str | None
    action_key: str


class AuditDefinition(TypedDict):
    alias: AuditAlias
    label: str
    route_slug: str | None
    job_kind: str | None
    action_key: str


class OperationName(StrEnum):
    CATALOG_AUDITS = "catalog_audits"
    CATALOG_AUDIT = "catalog_audit"
    ACCESS = "access"
    REPORTS_LIST = "reports_list"
    AUTH_STATUS = "auth_status"


class OperationPayload(TypedDict):
    name: str
    cli_command: str
    mcp_tool: str
    summary: str


@dataclass(frozen=True, slots=True)
class OperationSpec:
    name: OperationName
    cli_command: str
    mcp_tool: str
    summary: str
    execute: OperationExecutor


AUDITS: tuple[AuditDefinition, ...] = (
    {
        "alias": AuditAlias.SECURITY,
        "label": "Security",
        "route_slug": "vulns",
        "job_kind": "vuln-audit",
        "action_key": "audit.security",
    },
    {
        "alias": AuditAlias.AI_READINESS,
        "label": "AI readiness",
        "route_slug": "ai-readiness",
        "job_kind": "ai-maturity",
        "action_key": "audit.ai-readiness",
    },
    {
        "alias": AuditAlias.TESTS,
        "label": "Tests",
        "route_slug": "tests",
        "job_kind": "test-audit",
        "action_key": "audit.tests",
    },
    {
        "alias": AuditAlias.TECH_HEALTH,
        "label": "Codebase health",
        "route_slug": "tech-health",
        "job_kind": "tech-health",
        "action_key": "audit.tech-health",
    },
    {
        "alias": AuditAlias.DEPS,
        "label": "Dependency hygiene",
        "route_slug": "dependency-hygiene",
        "job_kind": "dependency-hygiene",
        "action_key": "audit.dependency-hygiene",
    },
    {
        "alias": AuditAlias.DEAD_CODE,
        "label": "Dead code",
        "route_slug": "dead-code",
        "job_kind": "dead-code",
        "action_key": "audit.dead-code",
    },
    {
        "alias": AuditAlias.RECON,
        "label": "Recon",
        "route_slug": None,
        "job_kind": None,
        "action_key": "audit.recon",
    },
)


def _catalog_audits_operation() -> list[AuditPayload]:
    return audit_catalog()


def _catalog_audit_operation(audit: AuditAlias) -> AuditPayload:
    return resolve_audit(audit)


def _access_operation() -> AccessPayload:
    return run_access()


def _reports_list_operation(
    selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    stale: bool = REPORTS_LIST_DEFAULT_STALE,
    min_severity: str | None = REPORTS_LIST_DEFAULT_MIN_SEVERITY,
) -> ReportsListPayload:
    return run_reports_list(selector=selector, stale=stale, min_severity=min_severity)


def _auth_status_operation(auth_file: Path | None = None) -> AuthStatusPayload:
    return run_auth_status(auth_file)


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
        cli_command="report list",
        mcp_tool="enji_reports_list",
        summary="List compact Enji Guard report summaries across repositories.",
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

_AUDIT_BY_ALIAS: dict[AuditAlias, AuditDefinition] = {audit["alias"]: audit for audit in AUDITS}
_OPERATION_BY_NAME: dict[OperationName, OperationSpec] = {spec.name: spec for spec in OPERATION_SPECS}


def package_version() -> str:
    return version("enji-guard-cli")


async def _await_operation_result[T](result: Awaitable[T]) -> T:
    return await result


def resolve_operation_result[T](result: T | Awaitable[T]) -> T:
    if inspect.isawaitable(result):
        return asyncio.run(_await_operation_result(result))
    return result


def audit_payload(audit: AuditDefinition) -> AuditPayload:
    return {
        "alias": audit["alias"].value,
        "label": audit["label"],
        "route_slug": audit["route_slug"],
        "job_kind": audit["job_kind"],
        "action_key": audit["action_key"],
    }


def audit_catalog() -> list[AuditPayload]:
    return [audit_payload(audit) for audit in AUDITS]


def resolve_audit(alias: AuditAlias) -> AuditPayload:
    audit = _AUDIT_BY_ALIAS.get(alias)
    if audit is None:
        raise ValueError(f"unknown audit alias: {alias}")
    return audit_payload(audit)


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
