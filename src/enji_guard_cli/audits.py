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
    route_slug: str | None
    job_kind: str | None
    action_key: str


AUDITS: tuple[AuditDefinition, ...] = (
    AuditDefinition(
        alias=AuditAlias.SECURITY,
        label="Security",
        route_slug="vulns",
        job_kind="vuln-audit",
        action_key="audit.security",
    ),
    AuditDefinition(
        alias=AuditAlias.AI_READINESS,
        label="AI readiness",
        route_slug="ai-readiness",
        job_kind="ai-maturity",
        action_key="audit.ai-readiness",
    ),
    AuditDefinition(
        alias=AuditAlias.TESTS,
        label="Tests",
        route_slug="tests",
        job_kind="test-audit",
        action_key="audit.tests",
    ),
    AuditDefinition(
        alias=AuditAlias.TECH_HEALTH,
        label="Codebase health",
        route_slug="tech-health",
        job_kind="tech-health",
        action_key="audit.tech-health",
    ),
    AuditDefinition(
        alias=AuditAlias.DEPS,
        label="Dependency hygiene",
        route_slug="dependency-hygiene",
        job_kind="dependency-hygiene",
        action_key="audit.dependency-hygiene",
    ),
    AuditDefinition(
        alias=AuditAlias.COGNITIVE_DEBT,
        label="Cognitive debt",
        route_slug="cognitive-debt",
        job_kind="cognitive-debt",
        action_key="audit.cognitive-debt",
    ),
    AuditDefinition(
        alias=AuditAlias.DEAD_CODE,
        label="Dead code",
        route_slug="dead-code",
        job_kind="dead-code",
        action_key="audit.dead-code",
    ),
    AuditDefinition(
        alias=AuditAlias.RECON,
        label="Recon",
        route_slug=None,
        job_kind=None,
        action_key="audit.recon",
    ),
)

REPORT_AUDITS: tuple[AuditDefinition, ...] = tuple(audit for audit in AUDITS if audit.route_slug is not None)
