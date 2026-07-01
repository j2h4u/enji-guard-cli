from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict


class AuditAlias(StrEnum):
    SECURITY = "security"
    AI_READINESS = "ai-readiness"
    TESTS = "tests"
    TECH_HEALTH = "tech-health"
    DEPS = "deps"
    COGNITIVE_DEBT = "cognitive-debt"
    DEAD_CODE = "dead-code"
    RECON = "recon"


class ReportAuditAlias(StrEnum):
    SECURITY = "security"
    AI_READINESS = "ai-readiness"
    TESTS = "tests"
    TECH_HEALTH = "tech-health"
    DEPS = "deps"
    COGNITIVE_DEBT = "cognitive-debt"
    DEAD_CODE = "dead-code"


class AuditPayload(TypedDict):
    alias: str
    label: str
    route_slug: str | None
    job_kind: str | None
    action_key: str


@dataclass(frozen=True, slots=True)
class AuditDefinition:
    alias: AuditAlias
    label: str
    action_key: str
    route_slug: str | None
    job_kind: str | None


@dataclass(frozen=True, slots=True)
class ReportAuditDefinition(AuditDefinition):
    route_slug: str
    job_kind: str


@dataclass(frozen=True, slots=True)
class ReconAuditDefinition(AuditDefinition):
    route_slug: None = None
    job_kind: None = None


AUDITS: tuple[AuditDefinition, ...] = (
    ReportAuditDefinition(
        alias=AuditAlias.SECURITY,
        label="Security",
        action_key="audit.security",
        route_slug="vulns",
        job_kind="vuln-audit",
    ),
    ReportAuditDefinition(
        alias=AuditAlias.AI_READINESS,
        label="AI readiness",
        action_key="audit.ai-readiness",
        route_slug="ai-readiness",
        job_kind="ai-maturity",
    ),
    ReportAuditDefinition(
        alias=AuditAlias.TESTS,
        label="Tests",
        action_key="audit.tests",
        route_slug="tests",
        job_kind="test-audit",
    ),
    ReportAuditDefinition(
        alias=AuditAlias.TECH_HEALTH,
        label="Codebase health",
        action_key="audit.tech-health",
        route_slug="tech-health",
        job_kind="tech-health",
    ),
    ReportAuditDefinition(
        alias=AuditAlias.DEPS,
        label="Dependency hygiene",
        action_key="audit.dependency-hygiene",
        route_slug="dependency-hygiene",
        job_kind="dependency-hygiene",
    ),
    ReportAuditDefinition(
        alias=AuditAlias.COGNITIVE_DEBT,
        label="Cognitive debt",
        action_key="audit.cognitive-debt",
        route_slug="cognitive-debt",
        job_kind="cognitive-debt",
    ),
    ReportAuditDefinition(
        alias=AuditAlias.DEAD_CODE,
        label="Dead code",
        action_key="audit.dead-code",
        route_slug="dead-code",
        job_kind="dead-code",
    ),
    ReconAuditDefinition(
        alias=AuditAlias.RECON,
        label="Recon",
        action_key="audit.recon",
    ),
)

REPORT_AUDITS: tuple[ReportAuditDefinition, ...] = tuple(
    audit for audit in AUDITS if isinstance(audit, ReportAuditDefinition)
)
REPORT_AUDIT_ALIASES: tuple[AuditAlias, ...] = tuple(audit.alias for audit in REPORT_AUDITS)
_AUDIT_BY_ALIAS: dict[AuditAlias, AuditDefinition] = {audit.alias: audit for audit in AUDITS}


def audit_payload(audit: AuditDefinition) -> AuditPayload:
    return {
        "alias": audit.alias.value,
        "label": audit.label,
        "route_slug": audit.route_slug,
        "job_kind": audit.job_kind,
        "action_key": audit.action_key,
    }


def audit_catalog() -> list[AuditPayload]:
    return [audit_payload(audit) for audit in AUDITS]


def resolve_audit(alias: AuditAlias) -> AuditDefinition:
    audit = _AUDIT_BY_ALIAS.get(alias)
    if audit is None:
        raise ValueError(f"unknown audit alias: {alias}")
    return audit


def require_report_audit(alias: AuditAlias) -> ReportAuditDefinition:
    audit = resolve_audit(alias)
    if not isinstance(audit, ReportAuditDefinition):
        raise ValueError("recon is not a report audit")
    return audit
