import pytest

from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.ports import (
    AuditProject,
    AuditRepository,
    AuditRun,
    AuditRunbookMetadata,
    AuditTaskBody,
    AuditWebsite,
)
from enji_guard_cli.audit.runs import AuditRunTaskContext, audit_run_task_body, skipped_audit_payload
from enji_guard_cli.audit.tasks import AuditTaskContext, task_for_repo
from enji_guard_cli.enji_gateway.audit_gateway import _fleet_task_body


def _context() -> AuditTaskContext:
    return AuditTaskContext(
        project=AuditProject(
            project_id="project_1",
            repositories=(AuditRepository("repo_1", "j2h4u/example", True),),
            linked_websites=(AuditWebsite("https://example.test", ("repo_1",)),),
        ),
        audit=AuditDefinition("audit.security", "Security", "vulns", "vuln-audit"),
        runbook=AuditRunbookMetadata("runbook_1", None, None, "single", {}),
        runbook_id="runbook_1",
        artifact_schema_name="upfront.audit.summary",
        artifact_schema_version="v1",
        description_template="Repo {{repository_full_name}}\n{{linked_websites}}",
    )


def test_audit_task_body_is_neutral_until_gateway_translation() -> None:
    body = task_for_repo(_context(), "repo_1")

    assert isinstance(body, AuditTaskBody)
    assert body.repository_full_name == "j2h4u/example"
    assert body.scope_owner == "project_1"
    assert not hasattr(body, "scope_type")
    assert body == AuditTaskBody(
        title="Security for j2h4u/example",
        description="Repo j2h4u/example\n- https://example.test",
        project_id="project_1",
        execution_flow="single",
        flow_config={},
        runbook_id="runbook_1",
        scope_owner="project_1",
        repository_full_name="j2h4u/example",
    )
    assert _fleet_task_body("audit.security", body) == {
        "title": "Security for j2h4u/example",
        "description": "Repo j2h4u/example\n- https://example.test",
        "project_id": "project_1",
        "execution_flow": "single",
        "flow_config": {},
        "runbook_id": "runbook_1",
        "scope_type": "project",
        "scope_owner": "project_1",
        "origin_type": "manual",
        "repo_access_contexts": [{"provider": "github", "repo_full_name": "j2h4u/example"}],
    }


def test_skipped_audit_result_keeps_typed_runs_until_delivery_projection() -> None:
    run = AuditRun("task-1", "audit.security", "running", None, None, None)

    result = skipped_audit_payload("audit.security", "audit.security", (run,))

    assert result["active_runs"] == [run]


@pytest.mark.parametrize(
    ("field", "message"),
    [("artifact_schema_name", "artifact schema name"), ("artifact_schema_version", "artifact schema version")],
)
def test_task_for_repo_rejects_missing_artifact_contract(field: str, message: str) -> None:
    context = _context()
    invalid = AuditTaskContext(
        project=context.project,
        audit=context.audit,
        runbook=context.runbook,
        runbook_id=context.runbook_id,
        artifact_schema_name="" if field == "artifact_schema_name" else context.artifact_schema_name,
        artifact_schema_version="" if field == "artifact_schema_version" else context.artifact_schema_version,
        description_template=context.description_template,
    )

    with pytest.raises(ValueError, match=message):
        task_for_repo(invalid, "repo_1")


def test_task_for_repo_rejects_incomplete_repository_full_name() -> None:
    context = _context()
    invalid = AuditTaskContext(
        project=AuditProject(
            project_id=context.project.project_id,
            repositories=(AuditRepository("repo_1", "/", True),),
            linked_websites=context.project.linked_websites,
        ),
        audit=context.audit,
        runbook=context.runbook,
        runbook_id=context.runbook_id,
        artifact_schema_name=context.artifact_schema_name,
        artifact_schema_version=context.artifact_schema_version,
        description_template=context.description_template,
    )

    with pytest.raises(ValueError, match="incomplete full name"):
        task_for_repo(invalid, "repo_1")


@pytest.mark.parametrize("runbook_id", ["", "   "])
def test_audit_run_task_body_rejects_blank_runbook_before_lookup(runbook_id: str) -> None:
    context = _context()
    invalid = AuditRunTaskContext(
        project_id="project_1",
        repo_id="repo_1",
        action_key="audit.security",
        project=context.project,
        catalog=AuditCatalog(
            published_audits=(),
            recon=AuditDefinition(
                "audit.security",
                "Security",
                "vulns",
                "vuln-audit",
                runbook_id=runbook_id,
                artifact_schema_name="upfront.audit.summary",
                artifact_schema_version="v1",
            ),
        ),
    )
    looked_up: list[str] = []

    with pytest.raises(ValueError, match="does not contain runbook"):
        audit_run_task_body(invalid, runbook=lambda value: looked_up.append(value) or context.runbook)

    assert looked_up == []


def test_task_for_repo_selects_requested_repository_from_multi_repo_project() -> None:
    context = _context()
    context = AuditTaskContext(
        project=AuditProject(
            project_id="project_1",
            repositories=(
                AuditRepository("repo_1", "j2h4u/first", True),
                AuditRepository("repo_2", "j2h4u/second", True),
            ),
            linked_websites=context.project.linked_websites,
        ),
        audit=context.audit,
        runbook=context.runbook,
        runbook_id=context.runbook_id,
        artifact_schema_name=context.artifact_schema_name,
        artifact_schema_version=context.artifact_schema_version,
        description_template=context.description_template,
    )

    assert task_for_repo(context, "repo_2").repository_full_name == "j2h4u/second"


def test_task_for_repo_uses_only_sites_linked_to_requested_repository() -> None:
    context = _context()
    context = AuditTaskContext(
        project=AuditProject(
            project_id="project_1",
            repositories=(
                AuditRepository("repo_1", "j2h4u/first", True),
                AuditRepository("repo_2", "j2h4u/second", True),
            ),
            linked_websites=(
                AuditWebsite("https://first.example", ("repo_1",)),
                AuditWebsite("https://second.example", ("repo_2",)),
            ),
        ),
        audit=context.audit,
        runbook=context.runbook,
        runbook_id=context.runbook_id,
        artifact_schema_name=context.artifact_schema_name,
        artifact_schema_version=context.artifact_schema_version,
        description_template="{{linked_websites}}",
    )

    assert task_for_repo(context, "repo_1").description == "- https://first.example"
