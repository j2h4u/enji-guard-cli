from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import httpx
import pytest

from enji_guard_cli.audit.errors import AuditMalformedError, AuditNotFoundError, AuditUpstreamError
from enji_guard_cli.audit.ports import (
    AuditArtifact,
    AuditCatalogResult,
    AuditRerunState,
    AuditRunbookMetadata,
    AuditRunRequest,
    AuditRunResult,
    AuditRunsResult,
    AuditTaskBody,
    AuditTaskDetail,
    AuditTaskLinksResult,
    MalformedAuditSnapshotError,
)
from enji_guard_cli.auth_session.adapters import AuthSessionAdapter
from enji_guard_cli.auth_session.api import import_bearer_token
from enji_guard_cli.enji_gateway import AuditGateway
from enji_guard_cli.enji_gateway.http import AuditRunCreate
from enji_guard_cli.enji_gateway.wire import audit_artifact_from_snapshot
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.json_types import JsonObjectPayload
from enji_guard_cli.transport import (
    EnjiHttpClient,
    EnjiHttpError,
    EnjiHttpRequest,
    EnjiHttpResponse,
    EnjiTransportError,
)


def test_audit_artifact_translates_only_explicit_application_fields() -> None:
    artifact = audit_artifact_from_snapshot(
        {
            "snapshot": {
                "content": {
                    "report": "# Findings\n\nNo issues found.",
                    "score": 98,
                    "generatedAt": "2026-07-15T08:00:00Z",
                    "metadata": {"report": "hostile replacement", "body": "hostile replacement"},
                    "arbitrary": "must stay at the wire boundary",
                }
            }
        },
        "audit.security",
    )

    assert artifact.audit_key == "audit.security"
    assert artifact.body == "# Findings\n\nNo issues found."
    assert artifact.score == 98
    assert artifact.generated_at == "2026-07-15T08:00:00Z"
    assert not hasattr(artifact, "metadata")


@pytest.mark.parametrize(
    ("snapshot", "message"),
    [
        ({}, "snapshot is missing snapshot content"),
        ({"snapshot": {}}, "snapshot is missing content"),
        ({"snapshot": {"content": {}}}, "snapshot content is missing a text body"),
        ({"snapshot": {"content": {"report": None}}}, "snapshot content is missing a text body"),
        ({"snapshot": {"content": {"report": ["not text"]}}}, "snapshot content is missing a text body"),
    ],
)
def test_audit_artifact_rejects_missing_or_malformed_report(snapshot: JsonObjectPayload, message: str) -> None:
    with pytest.raises(MalformedAuditSnapshotError, match=message):
        audit_artifact_from_snapshot(snapshot, "audit.security")


class _GatewayHarness:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.catalog_payload: JsonObjectPayload = {
            "curatedActions": [
                {
                    "actionKey": "audit.security",
                    "title": "Security audit",
                    "category": "security",
                    "status": "published",
                    "metricGroup": "security",
                    "runbookKind": "audit",
                }
            ]
        }
        self.active_runs_payload: JsonObjectPayload = {
            "activeRuns": [
                {
                    "fleetTaskId": "task-1",
                    "actionKey": "audit.security",
                    "status": "running",
                    "createdAt": "2026-07-15T08:00:00Z",
                    "startedAt": "2026-07-15T08:01:00Z",
                    "completedAt": None,
                }
            ]
        }
        self.rerun_payload: JsonObjectPayload = {
            "state": {
                "currentHeadSha": "abc",
                "lastAuditedSha": "def",
                "actions": {"audit.security": {"lastAuditedHeadSha": "def"}},
                "canRerun": True,
                "lastFleetTaskId": "task-0",
            }
        }
        self.links_payload: JsonObjectPayload = {
            "links": [
                {
                    "fleetTaskId": "task-1",
                    "actionKey": "audit.security",
                    "status": "running",
                    "artifactSchemaName": "upfront.audit.summary",
                    "createdAt": "2026-07-15T08:00:00Z",
                }
            ]
        }
        self.task_payload: JsonObjectPayload = {
            "task": {
                "id": "task-1",
                "status": "running",
                "createdAt": "2026-07-15T08:00:00Z",
                "startedAt": "2026-07-15T08:01:00Z",
            }
        }
        self.start_payload: JsonObjectPayload = {"task": {"id": "task-2", "status": "queued"}}
        self.snapshot_payload: JsonObjectPayload = {"snapshot": {"content": {"report": "findings", "score": 80}}}
        self.auth_file = Path("auth.json")
        self.client = cast(EnjiHttpClient, object())
        self.gateway = AuditGateway(auth_file=self.auth_file, client=self.client, auth_port=AuthSessionAdapter())
        self.request = AuditRunRequest(
            "repo-1",
            "project-1",
            "audit.security",
            AuditTaskBody(
                "Run security", "description", "project-1", "single", {}, "runbook-1", "project-1", "org/repo"
            ),
        )

    def fake_catalog(self, auth_file: object, client: object, *, auth_port: object) -> JsonObjectPayload:
        self.calls.append(("catalog", (auth_file, client)))
        return self.catalog_payload

    def fake_active_runs(
        self, repo_id: str, auth_file: object, client: object, *, auth_port: object
    ) -> JsonObjectPayload:
        self.calls.append(("active_runs", (repo_id, auth_file, client)))
        return self.active_runs_payload

    def fake_rerun_state(
        self, repo_id: str, auth_file: object, client: object, *, auth_port: object
    ) -> JsonObjectPayload:
        self.calls.append(("rerun_state", (repo_id, auth_file, client)))
        return self.rerun_payload

    def fake_task_links(
        self, repo_id: str, auth_file: object, client: object, *, auth_port: object
    ) -> JsonObjectPayload:
        self.calls.append(("task_links", (repo_id, auth_file, client)))
        return self.links_payload

    def fake_task_detail(
        self, task_id: str, auth_file: object, client: object, *, auth_port: object
    ) -> JsonObjectPayload:
        self.calls.append(("task_detail", (task_id, auth_file, client)))
        return self.task_payload

    def fake_runbook(
        self, runbook_id: str, auth_file: object, client: object, *, auth_port: object
    ) -> JsonObjectPayload:
        self.calls.append(("runbook", (runbook_id, auth_file, client)))
        return {"title": "Security audit", "suggested_flow": "single", "suggested_flow_config": {"retries": 1}}

    def fake_start_audit_run(
        self, request: AuditRunCreate, auth_file: object, client: object, *, auth_port: object
    ) -> JsonObjectPayload:
        self.calls.append(("start_audit_run", (request, auth_file, client)))
        return self.start_payload

    def fake_snapshot(
        self, repo_id: str, audit_key: str, auth_file: object, client: object, *, auth_port: object
    ) -> JsonObjectPayload:
        self.calls.append(("snapshot", (repo_id, audit_key, auth_file, client)))
        return self.snapshot_payload


@pytest.fixture
def gateway_harness(monkeypatch: pytest.MonkeyPatch) -> _GatewayHarness:
    harness = _GatewayHarness()
    import enji_guard_cli.enji_gateway.audit_gateway as gateway_module

    monkeypatch.setattr(gateway_module, "_catalog", harness.fake_catalog)
    monkeypatch.setattr(gateway_module, "_repo_active_runs", harness.fake_active_runs)
    monkeypatch.setattr(gateway_module, "_repo_audit_rerun_state", harness.fake_rerun_state)
    monkeypatch.setattr(gateway_module, "_repo_task_links", harness.fake_task_links)
    monkeypatch.setattr(gateway_module, "_task_detail", harness.fake_task_detail)
    monkeypatch.setattr(gateway_module, "_runbook", harness.fake_runbook)
    monkeypatch.setattr(gateway_module, "_start_audit_run", harness.fake_start_audit_run)
    monkeypatch.setattr(gateway_module, "_audit_summary_snapshot", harness.fake_snapshot)
    return harness


def test_audit_gateway_reads_catalog(gateway_harness: _GatewayHarness) -> None:
    catalog = gateway_harness.gateway.catalog()
    assert isinstance(catalog, AuditCatalogResult)
    assert catalog.actions[0].action_key == "audit.security"
    assert catalog.actions[0].title == "Security audit"
    assert gateway_harness.calls == [("catalog", (gateway_harness.auth_file, gateway_harness.client))]


def test_audit_gateway_reads_active_runs(gateway_harness: _GatewayHarness) -> None:
    active_runs = gateway_harness.gateway.active_runs("repo-1")
    assert isinstance(active_runs, AuditRunsResult)
    assert active_runs.runs[0].task_id == "task-1"
    assert active_runs.runs[0].status == "running"
    assert gateway_harness.calls == [("active_runs", ("repo-1", gateway_harness.auth_file, gateway_harness.client))]


def test_audit_gateway_drops_wire_extensions_from_active_run_projection(
    gateway_harness: _GatewayHarness,
) -> None:
    gateway_harness.active_runs_payload = {
        "activeRuns": [
            {
                "fleetTaskId": "task-root",
                "actionKey": "audit.security",
                "status": "running",
                "customField": {"source": "fleet"},
            }
        ]
    }

    run = gateway_harness.gateway.active_runs("repo-1").runs[0]

    assert run.task_id == "task-root"
    assert run.action_key == "audit.security"
    assert run.status == "running"
    assert run.created_at is None
    assert not hasattr(run, "upstream_payload")


def test_audit_gateway_reads_rerun_state(gateway_harness: _GatewayHarness) -> None:
    rerun_state = gateway_harness.gateway.rerun_state("repo-1")
    assert isinstance(rerun_state, AuditRerunState)
    assert rerun_state.current_head_sha == "abc"
    assert rerun_state.rerun_allowed is True
    assert rerun_state.audited_head_shas == {"audit.security": "def"}
    assert gateway_harness.calls == [("rerun_state", ("repo-1", gateway_harness.auth_file, gateway_harness.client))]


def test_audit_gateway_reads_task_links(gateway_harness: _GatewayHarness) -> None:
    task_links = gateway_harness.gateway.task_links("repo-1")
    assert isinstance(task_links, AuditTaskLinksResult)
    assert task_links.links[0].action_key == "audit.security"
    assert task_links.links[0].artifact_schema_name == "upfront.audit.summary"
    assert gateway_harness.calls == [("task_links", ("repo-1", gateway_harness.auth_file, gateway_harness.client))]


def test_audit_gateway_reads_task_detail(gateway_harness: _GatewayHarness) -> None:
    task_detail = gateway_harness.gateway.task_detail("task-1")
    assert isinstance(task_detail, AuditTaskDetail)
    assert task_detail.task_id == "task-1"
    assert task_detail.status == "running"
    assert task_detail.started_at == "2026-07-15T08:01:00Z"
    assert gateway_harness.calls == [("task_detail", ("task-1", gateway_harness.auth_file, gateway_harness.client))]


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(404, AuditNotFoundError), (503, AuditUpstreamError), (None, AuditUpstreamError)],
)
def test_audit_gateway_translates_task_detail_failures(
    gateway_harness: _GatewayHarness,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int | None,
    expected: type[Exception],
) -> None:
    import enji_guard_cli.enji_gateway.audit_gateway as gateway_module

    def fail(*args: object, **kwargs: object) -> JsonObjectPayload:
        raise EnjiApiError("UPSTREAM", "temporary failure", status_code=status_code)

    monkeypatch.setattr(gateway_module, "_task_detail", fail)
    with pytest.raises(expected):
        gateway_harness.gateway.task_detail("task-1")


def test_audit_gateway_rejects_malformed_task_detail(gateway_harness: _GatewayHarness) -> None:
    gateway_harness.task_payload = {"task": []}
    with pytest.raises(AuditMalformedError):
        gateway_harness.gateway.task_detail("task-1")


@dataclass
class _TaskDetailHttpFake:
    response: EnjiHttpResponse | EnjiHttpError
    requests: list[EnjiHttpRequest] = field(default_factory=list)

    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
        self.requests.append(request)
        if isinstance(self.response, EnjiHttpError):
            raise self.response
        return self.response


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (EnjiHttpResponse(status_code=404, headers={}, content=b"{}"), AuditNotFoundError),
        (EnjiHttpResponse(status_code=503, headers={}, content=b"{}"), AuditUpstreamError),
        (EnjiTransportError("task detail", httpx.ConnectError("offline")), AuditUpstreamError),
        (EnjiHttpResponse(status_code=200, headers={}, content=b"not-json"), AuditMalformedError),
        (EnjiHttpResponse(status_code=200, headers={}, content=b"[]"), AuditMalformedError),
    ],
)
def test_audit_gateway_task_detail_maps_real_http_helper_failures(
    tmp_path: Path,
    response: EnjiHttpResponse | EnjiHttpError,
    expected: type[Exception],
) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = _TaskDetailHttpFake(response)
    gateway = AuditGateway(auth_file=auth_file, client=client, auth_port=AuthSessionAdapter())

    with pytest.raises(expected):
        gateway.task_detail("task-1")

    assert [(request.method, request.url) for request in client.requests] == [
        ("GET", "https://fleet.enji.ai/api/v1/tasks/task-1"),
    ]


def test_audit_gateway_rejects_task_detail_identity_mismatch_from_real_http_helper(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = _TaskDetailHttpFake(EnjiHttpResponse(status_code=200, headers={}, content=b'{"task":{"id":"task-2"}}'))
    gateway = AuditGateway(auth_file=auth_file, client=client, auth_port=AuthSessionAdapter())

    with pytest.raises(AuditMalformedError, match="mismatched task id"):
        gateway.task_detail("task-1")


def test_audit_gateway_reads_runbook_metadata(gateway_harness: _GatewayHarness) -> None:
    runbook = gateway_harness.gateway.runbook_metadata("runbook-1")
    assert isinstance(runbook, AuditRunbookMetadata)
    assert runbook.runbook_id == "runbook-1"
    assert runbook.title == "Security audit"
    assert runbook.suggested_flow == "single"
    assert gateway_harness.calls == [("runbook", ("runbook-1", gateway_harness.auth_file, gateway_harness.client))]


def test_audit_gateway_starts_audit_run(gateway_harness: _GatewayHarness) -> None:
    started_run = gateway_harness.gateway.start_audit_run(gateway_harness.request)
    assert isinstance(started_run, AuditRunResult)
    assert started_run.task_id == "task-2"
    assert started_run.status == "queued"
    assert gateway_harness.calls == [
        (
            "start_audit_run",
            (
                AuditRunCreate(
                    "repo-1",
                    "project-1",
                    "audit.security",
                    {
                        "title": "Run security",
                        "description": "description",
                        "project_id": "project-1",
                        "execution_flow": "single",
                        "flow_config": {},
                        "runbook_id": "runbook-1",
                        "scope_type": "project",
                        "scope_owner": "project-1",
                        "origin_type": "manual",
                        "repo_access_contexts": [{"provider": "github", "repo_full_name": "org/repo"}],
                    },
                ),
                gateway_harness.auth_file,
                gateway_harness.client,
            ),
        )
    ]


def test_audit_gateway_resolves_external_schema_placeholder_at_wire_boundary(
    gateway_harness: _GatewayHarness,
) -> None:
    request = AuditRunRequest(
        "repo-1",
        "project-1",
        "audit.recon",
        AuditTaskBody(
            "Run recon", "schema={{reportSchemaName}}", "project-1", "single", {}, "runbook-1", "project-1", "org/repo"
        ),
    )

    gateway_harness.gateway.start_audit_run(request)

    create_request = gateway_harness.calls[-1][1][0]
    assert isinstance(create_request, AuditRunCreate)
    assert create_request.fleet_task_body["description"] == "schema=upfront.recon.report"


def test_audit_gateway_reads_snapshot(gateway_harness: _GatewayHarness) -> None:
    artifact = gateway_harness.gateway.read_audit_snapshot("repo-1", "audit.security")
    assert isinstance(artifact, AuditArtifact)
    assert artifact.audit_key == "audit.security"
    assert artifact.body == "findings"
    assert artifact.score == 80
    assert artifact.generated_at is None
    assert gateway_harness.calls == [
        ("snapshot", ("repo-1", "audit.security", gateway_harness.auth_file, gateway_harness.client))
    ]
