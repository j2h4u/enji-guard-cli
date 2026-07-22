from collections.abc import Callable
from typing import cast

import pytest

from enji_guard_cli.audit.artifacts import ArtifactReadItem, AuditArtifactUnavailableError, newer_run_for_report
from enji_guard_cli.audit.errors import AuditNotFoundError
from enji_guard_cli.audit.models import AuditCatalog, AuditDefinition
from enji_guard_cli.audit.observation import AuditRepositoryObservation
from enji_guard_cli.audit.ports import (
    AuditArtifact,
    AuditCatalogAction,
    AuditCatalogPort,
    AuditCatalogResult,
    AuditFreshness,
    AuditGatewayPort,
    AuditProject,
    AuditReportRef,
    AuditRerunState,
    AuditRun,
    AuditStatusItem,
)
from enji_guard_cli.audit.workflows import (
    AuditWorkflowDependencies,
    _catalog,
    _read_history_item,
    choose_audits,
    read_for_repo,
)


def _catalog_model() -> AuditCatalog:
    return AuditCatalog(
        published_audits=(AuditDefinition("audit.security", "Security", "vulns", "security"),),
        recon=AuditDefinition("audit.recon", "Recon", None, "recon"),
    )


def test_choose_audits_supports_all_and_selectors() -> None:
    catalog = _catalog_model()
    assert choose_audits(catalog, [], all_audits=True) == catalog.published_audits
    assert choose_audits(catalog, ["security"], all_audits=False) == catalog.published_audits


@pytest.mark.parametrize(
    ("selectors", "all_audits", "message"),
    [([], False, "at least one"), (["security"], True, "not both"), (["missing"], False, "unknown")],
)
def test_choose_audits_rejects_invalid_selection(selectors: list[str], all_audits: bool, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        choose_audits(_catalog_model(), selectors, all_audits=all_audits)


class _CatalogPort:
    def __init__(self, result: AuditCatalogResult) -> None:
        self.result = result
        self.calls = 0

    def catalog(self) -> AuditCatalogResult:
        self.calls += 1
        return self.result


def test_catalog_converts_live_actions_and_rejects_missing_recon() -> None:
    port = _CatalogPort(
        AuditCatalogResult(
            actions=(
                AuditCatalogAction("audit.recon", "Recon", "audit", "published", None, "recon"),
                AuditCatalogAction("audit.security", "Security", "audit", "published", "vulns", "security"),
                AuditCatalogAction("audit.hidden", "Hidden", "audit", "draft", None, None),
            )
        )
    )
    dependencies = AuditWorkflowDependencies(
        port, cast(AuditGatewayPort, object()), cast(Callable[[str], AuditProject], lambda _project: object())
    )
    result = _catalog(dependencies)
    assert [item.action_key for item in result.published_audits] == ["audit.security"]
    assert port.calls == 1

    missing = _CatalogPort(AuditCatalogResult(actions=()))
    with pytest.raises(ValueError, match=r"audit\.recon"):
        _catalog(
            AuditWorkflowDependencies(
                missing,
                cast(AuditGatewayPort, object()),
                cast(Callable[[str], AuditProject], lambda _project: object()),
            )
        )


def test_catalog_uses_frozen_observation_without_fetch() -> None:
    port = _CatalogPort(AuditCatalogResult(actions=()))
    frozen = _catalog_model()
    assert (
        _catalog(
            AuditWorkflowDependencies(
                port,
                cast(AuditGatewayPort, object()),
                cast(Callable[[str], AuditProject], lambda _project: object()),
                frozen,
            )
        )
        is frozen
    )
    assert port.calls == 0


def _report_ref(
    *, task_id: str | None = "task-old", completed_at: str | None = "2026-07-20T00:00:00+00:00"
) -> AuditReportRef:
    return AuditReportRef(task_id, completed_at, "2026-07-20T00:01:00+00:00", True)


def test_newer_run_for_report_only_matches_new_active_runs() -> None:
    ref = _report_ref()
    runs = (
        AuditRun("task-old", "audit.security", "running", "2026-07-22T00:00:00+00:00", None, None),
        AuditRun("old", "audit.security", "running", "2026-07-19T00:00:00+00:00", None, None),
        AuditRun("wrong-action", "audit.other", "running", "2026-07-22T00:00:00+00:00", None, None),
        AuditRun("done", "audit.security", "completed", "2026-07-22T00:00:00+00:00", None, None),
        AuditRun("task-new", "audit.security", "queued", "2026-07-21T00:00:00+00:00", None, None),
    )
    result = newer_run_for_report(ref, runs, action_key="audit.security")
    assert result is not None
    assert result.task_id == "task-new"
    assert result.status == "queued"

    running = AuditRun("task-running", "audit.security", "running", None, "2026-07-21T00:00:00+00:00", None)
    assert newer_run_for_report(ref, (running,), action_key="audit.security") is not None


@pytest.mark.parametrize(
    "started_at,created_at",
    [
        (None, None),
        (None, "not-a-time"),
        ("not-a-time", "2026-07-21T00:00:00+00:00"),
    ],
)
def test_newer_run_for_report_requires_parseable_later_timestamp(
    started_at: str | None, created_at: str | None
) -> None:
    run = AuditRun("task-new", "audit.security", "running", created_at, started_at, None)
    assert newer_run_for_report(_report_ref(), (run,), action_key="audit.security") is None


def test_newer_run_for_report_prefers_proven_newer_timestamp_over_unknown() -> None:
    unknown = AuditRun("unknown", "audit.security", "running", None, None, None)
    proven = AuditRun(
        "proven",
        "audit.security",
        "running",
        "2026-07-21T00:00:00+00:00",
        None,
        None,
    )
    result = newer_run_for_report(_report_ref(), (unknown, proven), action_key="audit.security")
    assert result is not None
    assert result.task_id == "proven"


def test_newer_run_for_report_requires_parseable_report_completion() -> None:
    ref = _report_ref(completed_at="not-a-time")
    timestamp_less = AuditRun("task-new", "audit.security", "running", None, None, None)
    assert newer_run_for_report(ref, (timestamp_less,), action_key="audit.security") is None


def test_stale_report_marks_reused_timestamp_less_active_task_as_newer() -> None:
    ref = _report_ref()
    reused = AuditRun("task-old", "audit.security", "pending", None, None, None)

    result = newer_run_for_report(
        ref,
        (reused,),
        action_key="audit.security",
        report_is_stale=True,
    )

    assert result is not None
    assert result.task_id == "task-old"
    assert result.state == "queued"


def _status_item() -> AuditStatusItem:
    return AuditStatusItem(
        "audit.security",
        "Security",
        AuditFreshness("head", "head", "fresh"),
        False,
        "none",
        None,
        None,
    )


class _HistoryGateway:
    def __init__(
        self, refs: tuple[AuditReportRef, ...], *, missing_snapshot: bool = False, missing_reports: bool = False
    ) -> None:
        self.refs = refs
        self.missing_snapshot = missing_snapshot
        self.missing_reports = missing_reports
        self.report_groups: list[str] = []

    def list_audit_reports(self, _repo_id: str, _metric_group: str) -> tuple[AuditReportRef, ...]:
        self.report_groups.append(_metric_group)
        if self.missing_reports:
            raise AuditNotFoundError
        return self.refs

    def read_audit_snapshot(self, _repo_id: str, _audit_key: str, _metric_group: str, *, task_id: str) -> AuditArtifact:
        if self.missing_snapshot:
            raise AuditNotFoundError
        return AuditArtifact("audit.security", f"body for {task_id}", score=95)


def _observation(*runs: AuditRun) -> AuditRepositoryObservation:
    return AuditRepositoryObservation((), runs, AuditRerunState(None, None, None, None))


def test_read_history_item_reads_selected_report_and_marks_newer_run() -> None:
    gateway = _HistoryGateway((_report_ref(),))
    item = _read_history_item(
        "repo-1",
        _status_item(),
        _catalog_model().published_audits[0],
        _observation(AuditRun("task-new", "audit.security", "running", "2026-07-21T00:00:00+00:00", None, None)),
        cast(AuditGatewayPort, gateway),
        tolerate_unavailable=True,
    )
    assert item.available is True
    assert item.artifact is not None
    assert item.artifact.task_id == "task-old"
    assert item.newer_run is not None


@pytest.mark.parametrize(
    "gateway, tolerate, raises",
    [
        (_HistoryGateway((), missing_reports=True), True, False),
        (_HistoryGateway((_report_ref(),), missing_snapshot=True), True, False),
        (_HistoryGateway((AuditReportRef(None, None, None, False),)), True, False),
        (_HistoryGateway((), missing_reports=True), False, True),
    ],
)
def test_read_history_item_handles_unavailable_history(gateway: _HistoryGateway, tolerate: bool, raises: bool) -> None:
    def call() -> ArtifactReadItem:
        return _read_history_item(
            "repo-1",
            _status_item(),
            _catalog_model().published_audits[0],
            _observation(),
            cast(AuditGatewayPort, gateway),
            tolerate_unavailable=tolerate,
        )

    if raises:
        with pytest.raises(AuditArtifactUnavailableError, match="artifact_not_found"):
            call()
    else:
        result = call()
        assert result.available is False
        assert result.reason == "artifact_not_found"


def test_read_history_item_falls_back_to_action_key_when_metric_group_is_missing() -> None:
    gateway = _HistoryGateway((_report_ref(),))
    result = _read_history_item(
        "repo-1",
        _status_item(),
        AuditDefinition("audit.security", "Security", None, "security"),
        _observation(),
        cast(AuditGatewayPort, gateway),
        tolerate_unavailable=True,
    )
    assert result.available is True
    assert gateway.report_groups == ["audit.security"]


def test_read_for_repo_default_resolves_history_before_filtering_status() -> None:
    gateway = _HistoryGateway((_report_ref(),))
    dependencies = AuditWorkflowDependencies(
        cast(AuditCatalogPort, object()),
        cast(AuditGatewayPort, gateway),
        cast(Callable[[str], AuditProject], lambda _repo: object()),
        frozen_catalog=_catalog_model(),
        repository_observation=lambda _repo: _observation(),
    )
    result = read_for_repo("repo-1", [], all_audits=False, dependencies=dependencies)
    assert [item.audit_key for item in result] == ["audit.security"]
