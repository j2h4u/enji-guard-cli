import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from enji_guard_cli._enji_api_contract import (
    IMPROVEMENT_JOB_PUT_ENDPOINT_SPEC,
    IMPROVEMENT_JOBS_ENDPOINT_SPEC,
    implemented_api_endpoints,
)
from enji_guard_cli.auth import import_bearer_token
from enji_guard_cli.enji_api import (
    AuditRunCreate,
    RepoTransfer,
    access,
    add_project_repo,
    audit_summary_snapshot,
    catalog,
    connect_project_repo,
    create_project,
    delete_project,
    delete_project_repo,
    improvement_jobs,
    move_repo,
    preflight_repo_move,
    project_detail,
    put_improvement_job,
    rename_project,
    repo_active_runs,
    repo_audit_rerun_state,
    repo_task_links,
    reports_list,
    runbook,
    start_audit_run,
    task_detail,
)
from enji_guard_cli.transport import EnjiHttpRequest, EnjiHttpResponse

CONTRACT_PATH = Path("contracts/enji-openapi.json")
HTTP_METHODS = frozenset({"get", "put", "post", "patch", "delete", "head", "options", "trace"})


@dataclass
class FakeEnjiHttpClient:
    responses: list[EnjiHttpResponse]
    requests: list[EnjiHttpRequest] = field(default_factory=list)

    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def test_implemented_enji_api_paths_exist_in_openapi_contract(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"limits": {}}),
            json_response({"projects": []}),
            json_response({"project": {"id": "project_1"}, "repos": [], "webResources": []}),
            json_response({"id": "project_1"}, status_code=201),
            json_response({"project": {"id": "project_1"}}, status_code=201),
            json_response({"project": {"id": "project_1", "name": "Friends"}}),
            empty_response(status_code=204),
            empty_response(status_code=204),
            empty_response(status_code=204),
            json_response({"repo": {"id": "repo_1"}}),
            json_response({"curatedActions": []}),
            json_response({"id": "runbook_1", "suggested_flow": "single"}),
            json_response({"repo": {"id": "repo_1"}}, status_code=201),
            empty_response(status_code=204),
            json_response({"repo": {"id": "repo_1", "connected": True}}),
            json_response({"activeRuns": []}),
            json_response({"state": {}}),
            json_response({"links": []}),
            json_response({"task": {"id": "task_1"}}),
            json_response({"task": {"id": "task_1"}}, status_code=201),
            json_response({"snapshot": {"content": {"report": "ok"}}}),
            json_response({"jobs": []}),
            json_response({"job": {"enabled": True}}),
        ]
    )

    access(auth_file, client)
    reports_list(auth_file, client)
    project_detail("project_1", auth_file, client)
    create_project("Pets", auth_file, client)
    rename_project("project_1", "Friends", auth_file, client)
    delete_project("project_1", auth_file, client)
    preflight_repo_move("project_1", "repo_1", "project_2", auth_file, client)
    move_repo(RepoTransfer("project_1", "repo_1", "project_2"), auth_file, client)
    catalog(auth_file, client)
    runbook("runbook_1", auth_file, client)
    add_project_repo("project_1", "j2h4u", "enji-guard-cli", auth_file, client)
    delete_project_repo("project_1", "repo_1", auth_file, client)
    connect_project_repo("project_1", "repo_1", auth_file, client)
    repo_active_runs("repo_1", auth_file, client)
    repo_audit_rerun_state("repo_1", auth_file, client)
    repo_task_links("repo_1", auth_file, client)
    task_detail("task_1", auth_file, client)
    start_audit_run(
        AuditRunCreate(
            repo_id="repo_1",
            project_id="project_1",
            action_key="audit.recon",
            fleet_task_body={"title": "Run recon"},
        ),
        auth_file,
        client,
    )
    audit_summary_snapshot("repo_1", "vulns", auth_file, client)
    improvement_jobs("repo_1", auth_file, client)
    put_improvement_job("repo_1", "vuln-audit", {"enabled": True}, auth_file, client)

    contract = cast(object, json.loads(CONTRACT_PATH.read_text(encoding="utf-8")))
    assert isinstance(contract, dict)
    paths = contract.get("paths")
    assert isinstance(paths, dict)
    contract_operations = {
        (method.upper(), path)
        for path, raw_path_item in paths.items()
        if isinstance(path, str) and isinstance(raw_path_item, dict)
        for method in raw_path_item
        if method in HTTP_METHODS
    }
    requested_operations = [(request.method.upper(), urlsplit(request.url).path) for request in client.requests]

    assert [
        (method, path)
        for method, path in requested_operations
        if not _has_contract_operation(contract_operations, method, path)
    ] == []


def test_implemented_endpoint_specs_match_openapi_contract() -> None:
    contract = cast(object, json.loads(CONTRACT_PATH.read_text(encoding="utf-8")))
    assert isinstance(contract, dict)
    paths = contract.get("paths")
    assert isinstance(paths, dict)

    for endpoint in implemented_api_endpoints():
        path_template = endpoint["path_template"]
        method = endpoint["method"].lower()
        assert path_template in paths
        path_item = paths[path_template]
        assert isinstance(path_item, dict)
        operation = path_item.get(method)
        assert isinstance(operation, dict)
        assert operation.get("operationId") == endpoint["operation_id"]
        assert _request_body_ref(operation) == endpoint["request_body_ref"]


def test_catalog_contract_models_live_curated_actions_without_closed_key_or_kind_enums() -> None:
    contract = cast(object, json.loads(CONTRACT_PATH.read_text(encoding="utf-8")))
    assert isinstance(contract, dict)
    paths = contract.get("paths")
    assert isinstance(paths, dict)
    catalog_path = paths.get("/api/ux/catalog")
    assert isinstance(catalog_path, dict)
    catalog_operation = catalog_path.get("get")
    assert isinstance(catalog_operation, dict)
    catalog_response = _response_schema(catalog_operation, "200")
    assert catalog_response == {"$ref": "#/components/schemas/CatalogResponse"}

    components = contract.get("components")
    assert isinstance(components, dict)
    schemas = components.get("schemas")
    assert isinstance(schemas, dict)
    catalog_schema = schemas.get("CatalogResponse")
    assert isinstance(catalog_schema, dict)
    curated_actions = _schema_property(catalog_schema, "curatedActions")
    assert curated_actions.get("items") == {"$ref": "#/components/schemas/CuratedAction"}
    action_schema = schemas.get("CuratedAction")
    assert isinstance(action_schema, dict)
    for property_name in ("actionKey", "runbookKind"):
        property_schema = _schema_property(action_schema, property_name)
        assert property_schema.get("type") == "string"
        assert "enum" not in property_schema

    autofixes = _schema_property(catalog_schema, "auditAutofixes")
    assert autofixes.get("items") == {"$ref": "#/components/schemas/AuditAutofix"}
    autofix_schema = schemas.get("AuditAutofix")
    assert isinstance(autofix_schema, dict)
    assert set(autofix_schema.get("required", [])) == {
        "actionKey",
        "variantKey",
        "title",
        "description",
        "fleetRunbookId",
        "status",
        "sortOrder",
    }
    autofix_properties = autofix_schema.get("properties")
    assert isinstance(autofix_properties, dict)
    assert autofix_properties["sortOrder"] == {"type": "integer"}
    for property_name in ("actionKey", "variantKey", "title", "description", "fleetRunbookId", "status"):
        assert autofix_properties[property_name].get("type") == "string"


def test_improvement_job_summaries_describe_autofix_management() -> None:
    contract = cast(object, json.loads(CONTRACT_PATH.read_text(encoding="utf-8")))
    assert isinstance(contract, dict)
    paths = contract.get("paths")
    assert isinstance(paths, dict)
    expected_summaries = {
        ("get", "/api/ux/improvement-jobs/{repoId}"): "Return all autofix improvement jobs for a repository.",
        ("put", "/api/ux/improvement-jobs/{repoId}/{kind}"): "Create or update an autofix improvement job.",
        ("post", "/api/ux/improvement-jobs/{repoId}/{kind}/resume"): "Resume a paused autofix improvement job.",
        (
            "put",
            "/api/ux/improvement-jobs/{repoId}/{kind}/binding",
        ): "Bind an autofix improvement job to a Fleet schedule.",
    }
    for (method, path), summary in expected_summaries.items():
        path_item = paths.get(path)
        assert isinstance(path_item, dict)
        operation = path_item.get(method)
        assert isinstance(operation, dict)
        assert operation.get("summary") == summary


def test_improvement_job_endpoint_operations_use_the_autofix_ontology() -> None:
    assert IMPROVEMENT_JOBS_ENDPOINT_SPEC.operation == "autofix list"
    assert IMPROVEMENT_JOB_PUT_ENDPOINT_SPEC.operation == "autofix set"


def test_audit_auto_run_contract_models_dynamic_action_keys_and_subscriptions() -> None:
    contract = cast(object, json.loads(CONTRACT_PATH.read_text(encoding="utf-8")))
    assert isinstance(contract, dict)
    paths = contract.get("paths")
    assert isinstance(paths, dict)

    collection = paths.get("/api/ux/repos/{repoId}/audit-auto-runs")
    assert isinstance(collection, dict)
    collection_get = collection.get("get")
    assert isinstance(collection_get, dict)
    assert collection_get.get("operationId") == "listRepoAuditAutoRuns"
    assert _response_schema(collection_get, "200") == {"$ref": "#/components/schemas/AuditAutoRunSubscriptionsResponse"}

    action = paths.get("/api/ux/repos/{repoId}/audit-auto-runs/{actionKey}")
    assert isinstance(action, dict)
    action_put = action.get("put")
    assert isinstance(action_put, dict)
    assert action_put.get("operationId") == "putRepoAuditAutoRun"
    assert _request_body_ref(action_put) == "#/components/requestBodies/AuditAutoRunSubscriptionUpdate"
    assert _response_schema(action_put, "200") == {"$ref": "#/components/schemas/AuditAutoRunSubscriptionResponse"}

    components = contract.get("components")
    assert isinstance(components, dict)
    schemas = components.get("schemas")
    assert isinstance(schemas, dict)
    action_key = schemas.get("AuditActionKey")
    assert isinstance(action_key, dict)
    assert action_key.get("type") == "string"
    assert "enum" not in action_key

    update = schemas.get("AuditAutoRunSubscriptionUpdate")
    assert isinstance(update, dict)
    assert set(update.get("required", [])) == {
        "enabled",
        "cadence",
        "scheduleDay",
        "scheduleDayOfMonth",
        "scheduleTime",
        "scheduleTimeSource",
        "timezone",
        "windowDays",
        "windowEndTime",
        "windowMode",
        "windowStartTime",
    }
    properties = update.get("properties")
    assert isinstance(properties, dict)
    assert set(properties) >= set(update["required"])
    for property_name in ("scheduleDay", "windowEndTime", "windowStartTime"):
        field_schema = properties.get(property_name)
        assert isinstance(field_schema, dict)
        assert "null" in field_schema.get("type", [])

    subscription = schemas.get("AuditAutoRunSubscription")
    assert isinstance(subscription, dict)
    assert subscription.get("allOf", [])[1]["properties"]["actionKey"] == {
        "$ref": "#/components/schemas/AuditActionKey"
    }
    response = schemas.get("AuditAutoRunSubscriptionResponse")
    assert isinstance(response, dict)
    assert _schema_property(response, "subscription") == {"$ref": "#/components/schemas/AuditAutoRunSubscription"}


def test_reconstructed_extended_surfaces_remain_in_openapi_contract() -> None:
    contract = cast(object, json.loads(CONTRACT_PATH.read_text(encoding="utf-8")))
    assert isinstance(contract, dict)
    paths = contract.get("paths")
    assert isinstance(paths, dict)

    expected_operations = {
        ("post", "/api/ux/improvement-jobs/{repoId}/{kind}/tried"),
        ("post", "/api/ux/repos/{repoId}/improvement-runs"),
        ("put", "/api/ux/repos/{repoId}/audit-findings"),
        ("post", "/api/ux/repos/{repoId}/audit-findings/{findingId}/autofix-result"),
        ("put", "/api/ux/pentest-consents/{consentId}"),
        ("post", "/api/ux/repos/{repoId}/pentest-runs"),
        ("put", "/api/ux/pentest-jobs/{repoId}/{kind}"),
        ("delete", "/api/ux/pentest-jobs/{repoId}/{kind}"),
        ("put", "/api/ux/pentest-jobs/{repoId}/{kind}/binding"),
        ("get", "/api/ux/projects/{projectId}/publication"),
        ("put", "/api/ux/projects/{projectId}/publication"),
        ("get", "/api/ux/public/projects/{projectId}"),
        ("get", "/api/ux/public/projects/{projectId}/repos/{repoId}/dashboard-data"),
        ("get", "/api/ux/public/projects/{projectId}/repos/{repoId}/audit-history"),
        ("get", "/api/ux/public/projects/{projectId}/repos/{repoId}/snapshots/{snapshotId}"),
        ("delete", "/api/v1/projects/{projectId}/members/{userId}"),
    }

    assert {
        (method, path)
        for method, path in expected_operations
        if not isinstance(paths.get(path), dict) or method not in paths[path]
    } == set()


def json_response(payload: object, *, status_code: int = 200) -> EnjiHttpResponse:
    return EnjiHttpResponse(
        status_code=status_code,
        headers={},
        content=json.dumps(payload).encode("utf-8"),
    )


def empty_response(*, status_code: int = 204) -> EnjiHttpResponse:
    return EnjiHttpResponse(status_code=status_code, headers={}, content=b"")


def _has_contract_operation(contract_operations: set[tuple[str, str]], method: str, path: str) -> bool:
    return any(
        contract_method == method and _path_template_matches(contract_path, path)
        for contract_method, contract_path in contract_operations
    )


def _path_template_matches(template: str, path: str) -> bool:
    template_parts = template.strip("/").split("/")
    path_parts = path.strip("/").split("/")
    if len(template_parts) != len(path_parts):
        return False
    return all(
        _path_segment_matches(template_part, path_part)
        for template_part, path_part in zip(template_parts, path_parts, strict=True)
    )


def _path_segment_matches(template_part: str, path_part: str) -> bool:
    if template_part.startswith("{") and template_part.endswith("}"):
        return bool(path_part)
    return template_part == path_part


def _request_body_ref(operation: dict[str, object]) -> str | None:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return None
    ref = request_body.get("$ref")
    if isinstance(ref, str):
        return ref
    content = request_body.get("content")
    if not isinstance(content, dict):
        return None
    json_content = content.get("application/json")
    if not isinstance(json_content, dict):
        return None
    schema = json_content.get("schema")
    if not isinstance(schema, dict):
        return None
    schema_ref = schema.get("$ref")
    return schema_ref if isinstance(schema_ref, str) else None


def _response_schema(operation: dict[str, object], status_code: str) -> dict[str, object]:
    responses = operation.get("responses")
    assert isinstance(responses, dict)
    response = responses.get(status_code)
    assert isinstance(response, dict)
    content = response.get("content")
    assert isinstance(content, dict)
    json_content = content.get("application/json")
    assert isinstance(json_content, dict)
    schema = json_content.get("schema")
    assert isinstance(schema, dict)
    return schema


def _schema_property(schema: dict[str, object], name: str) -> dict[str, object]:
    properties = schema.get("properties")
    assert isinstance(properties, dict)
    property_schema = properties.get(name)
    assert isinstance(property_schema, dict)
    return property_schema
