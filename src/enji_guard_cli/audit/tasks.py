"""Audit task construction use-cases."""

from dataclasses import dataclass

from enji_guard_cli.audit.models import AuditDefinition
from enji_guard_cli.audit.ports import AuditProject, AuditRunbookMetadata, AuditTaskBody

DEFAULT_EXECUTION_FLOW = "single"


@dataclass(frozen=True, slots=True)
class AuditTaskContext:
    project: AuditProject
    audit: AuditDefinition
    runbook: AuditRunbookMetadata
    runbook_id: str
    artifact_schema_name: str
    artifact_schema_version: str
    description_template: str | None = None
    repo_id: str | None = None


def build_task_body(context: AuditTaskContext) -> AuditTaskBody:
    if context.repo_id is None:
        raise ValueError("audit task requires a repository id")
    return task_for_repo(context, context.repo_id)


def task_for_repo(context: AuditTaskContext, repo_id: str) -> AuditTaskBody:
    repository = next((repo for repo in context.project.repositories if repo.repo_id == repo_id), None)
    if repository is None:
        raise ValueError(f"project does not contain repo id: {repo_id}")
    if repository.connected is not True:
        raise ValueError(f"repo is not connected: {repository.locator}")
    locator = repository.locator
    if not isinstance(locator, str) or not locator.strip() or locator in {"/", "//"}:
        raise ValueError(f"repository has incomplete locator: {locator!r}")
    if not isinstance(context.artifact_schema_name, str) or not context.artifact_schema_name.strip():
        raise ValueError("audit task is missing artifact schema name")
    if not isinstance(context.artifact_schema_version, str) or not context.artifact_schema_version.strip():
        raise ValueError("audit task is missing artifact schema version")
    if not isinstance(context.runbook_id, str) or not context.runbook_id.strip():
        raise ValueError("audit task is missing runbook id")
    return AuditTaskBody(
        title=f"{context.audit.title} for {locator}",
        description=_description(context, locator, repo_id, repository.web_url),
        project_id=context.project.project_id,
        execution_flow=context.runbook.suggested_flow or DEFAULT_EXECUTION_FLOW,
        flow_config=context.runbook.suggested_flow_config,
        runbook_id=context.runbook_id,
        scope_owner=context.project.project_id,
        repository_provider=repository.provider,
        repository_locator=locator,
        repository_web_url=repository.web_url,
    )


def _description(
    context: AuditTaskContext,
    locator: str,
    repo_id: str | None = None,
    web_url: str | None = None,
) -> str:
    template = context.description_template or _default_template()
    linked = [site.url for site in context.project.linked_websites if repo_id in site.repo_ids]
    values = {
        "recurring_prefix": f"Task created from {context.audit.action_key} for {locator}.",
        "repository_locator": locator,
        "repository_url": web_url or "",
        "linked_websites": "\n".join(f"- {url}" for url in linked) if linked else "- none linked yet",
        "artifact_schema_name": context.artifact_schema_name,
        "artifact_schema_version": context.artifact_schema_version,
        "artifact_contract": "structured artifact metadata is machine-readable and deterministic",
        "constraints": "- use task title and description only",
    }
    for name, value in values.items():
        template = template.replace(f"{{{{{name}}}}}", value)
    return template


def _default_template() -> str:
    return (
        "{{recurring_prefix}}\n\nRepository:\n- locator: {{repository_locator}}\n"
        "- url: {{repository_url}}\n\nLinked websites:\n{{linked_websites}}\n\n"
        "Artifact contract for this run:\n- structured artifact metadata.schema_name={{artifact_schema_name}}\n"
        "- structured artifact metadata.schema_version={{artifact_schema_version}}\n"
        "- {{artifact_contract}}\n\nConstraints:\n{{constraints}}"
    )
