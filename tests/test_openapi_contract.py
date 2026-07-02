import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from enji_guard_cli._enji_api_contract import implemented_api_endpoints
from enji_guard_cli.auth import import_bearer_token
from enji_guard_cli.enji_api import (
    AuditRunCreate,
    RepoTransfer,
    _connect_project_repo,
    access,
    audit_summary_snapshot,
    catalog,
    create_project,
    delete_project,
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
            json_response({"activeRuns": []}),
            json_response({"state": {}}),
            json_response({"links": []}),
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
    _connect_project_repo("project_1", "j2h4u", "enji-guard-cli", auth_file, client)
    repo_active_runs("repo_1", auth_file, client)
    repo_audit_rerun_state("repo_1", auth_file, client)
    repo_task_links("repo_1", auth_file, client)
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
