import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

import enji_guard_cli.core as core
import enji_guard_cli.enji_api as enji_api
from enji_guard_cli.cli import app
from enji_guard_cli.enji_api_impl import audit_catalog_snapshot
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue
from enji_guard_cli.settings import default_settings


def test_audit_catalog_snapshot_baselines_and_detects_audit_changes(tmp_path: Path) -> None:
    state_file = tmp_path / "state" / "audit-catalog.json"
    initial = _catalog(
        _action("audit.security", title="Security", metric_group="vulns"),
        _action("audit.tests", title="Tests", metric_group="tests"),
    )
    current = _catalog(
        _action("audit.security", title="Application security", metric_group="vulns"),
        _action("audit.ai-readiness", title="AI readiness", metric_group="ai-readiness"),
    )

    assert audit_catalog_snapshot.observe_audit_catalog(initial, state_file) == ()
    assert state_file.exists()
    assert state_file.stat().st_mode & 0o777 == 0o600
    snapshot = cast(dict[str, object], json.loads(state_file.read_text(encoding="utf-8")))
    audits = cast(dict[str, object], snapshot["audits"])
    assert set(audits) == {"audit.security", "audit.tests"}
    assert "audit.recon" not in audits

    changes = audit_catalog_snapshot.observe_audit_catalog(current, state_file)

    assert [(change.kind, change.selector) for change in changes] == [
        ("added", "ai-readiness"),
        ("removed", "tests"),
        ("changed", "security"),
    ]
    assert changes[-1].changed_fields == ("title",)
    assert audit_catalog_snapshot.observe_audit_catalog(current, state_file) == ()


def test_malformed_upstream_audit_catalog_preserves_previous_observation(tmp_path: Path) -> None:
    state_file = tmp_path / "state" / "audit-catalog.json"
    baseline = _catalog(_action("audit.security", title="Security", metric_group="vulns"))
    added = _catalog(
        _action("audit.security", title="Security", metric_group="vulns"),
        _action("audit.open-source", title="Open source", metric_group="open-source"),
    )

    assert audit_catalog_snapshot.observe_audit_catalog(baseline, state_file) == ()
    assert audit_catalog_snapshot.observe_audit_catalog({"curatedActions": []}, state_file) == ()

    changes = audit_catalog_snapshot.observe_audit_catalog(added, state_file)

    assert [(change.kind, change.selector) for change in changes] == [("added", "open-source")]


def test_active_audit_catalog_observation_notifies_only_after_a_live_catalog_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payloads = [
        _catalog(_action("audit.security", title="Security", metric_group="vulns")),
        _catalog(_action("audit.security", title="Application security", metric_group="vulns")),
    ]
    monkeypatch.setattr(enji_api, "run_api_request", lambda *_args: payloads.pop(0))
    events: list[tuple[audit_catalog_snapshot.AuditCatalogChange, ...]] = []
    token = audit_catalog_snapshot.begin_audit_catalog_observation(
        state_file=tmp_path / "audit-catalog.json",
        notifier=events.append,
    )

    try:
        enji_api.catalog()
        enji_api.catalog()
    finally:
        audit_catalog_snapshot.end_audit_catalog_observation(token)

    assert len(events) == 1
    assert [(change.kind, change.selector) for change in events[0]] == [("changed", "security")]


def test_catalog_audits_json_baseline_repeat_and_added_audit_use_audit_catalog_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = default_settings()
    state_file = tmp_path / "json" / "audit-catalog.json"
    selected_state_file = state_file
    monkeypatch.setattr(
        core,
        "default_settings",
        lambda: replace(settings, audit_catalog=replace(settings.audit_catalog, state_file=selected_state_file)),
    )
    baseline = _catalog(_action("audit.security", title="Security", metric_group="vulns"))
    added = _catalog(
        _action("audit.security", title="Security", metric_group="vulns"),
        _action("audit.open-source", title="Open source", metric_group="open-source"),
    )
    payloads = [baseline, baseline, added, baseline, added]
    monkeypatch.setattr(enji_api, "run_api_request", lambda *_args: payloads.pop(0))

    first = CliRunner().invoke(app, ["catalog", "audits", "--json"])
    second = CliRunner().invoke(app, ["catalog", "audits", "--json"])
    third = CliRunner().invoke(app, ["catalog", "audits", "--json"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert first.stderr == ""
    assert second.stderr == ""
    first_output = _catalog_audits_output(first.stdout)
    second_output = _catalog_audits_output(second.stdout)
    assert first_output["audit_catalog"] == {"changes": []}
    assert second_output["audit_catalog"] == {"changes": []}
    assert set(first_output) == {"audits", "audit_catalog"}
    assert "audit.recon" in {audit["action_key"] for audit in cast(list[dict[str, object]], first_output["audits"])}

    assert third.exit_code == 0
    assert third.stderr == ""
    third_output = _catalog_audits_output(third.stdout)
    assert set(third_output) == {"audits", "audit_catalog"}
    changes = cast(list[dict[str, object]], cast(dict[str, object], third_output["audit_catalog"])["changes"])
    assert changes == [
        {
            "action_key": "audit.open-source",
            "changed_fields": [],
            "current": {
                "actionKey": "audit.open-source",
                "category": "audit",
                "metricGroup": "open-source",
                "runbookKind": "open-source-audit",
                "status": "published",
                "title": "Open source",
            },
            "kind": "added",
            "previous": None,
            "selector": "open-source",
        }
    ]

    selected_state_file = tmp_path / "human" / "audit-catalog.json"
    human_baseline = CliRunner().invoke(app, ["catalog", "audits"])
    human_added = CliRunner().invoke(app, ["catalog", "audits"])

    assert human_baseline.exit_code == 0
    assert human_added.exit_code == 0
    assert human_baseline.stderr == ""
    assert human_added.stderr == ""
    assert 'audit catalog changed: added audit open-source ("Open source")' in human_added.stdout


def _catalog_audits_output(stdout: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(stdout))


def _action(action_key: str, *, title: str, metric_group: str) -> JsonObjectPayload:
    return {
        "actionKey": action_key,
        "title": title,
        "category": "audit",
        "status": "published",
        "metricGroup": metric_group,
        "runbookKind": f"{metric_group}-audit",
    }


def _catalog(*actions: JsonObjectPayload) -> JsonObjectPayload:
    curated_actions: list[JsonValue] = [
        {
            "actionKey": "audit.recon",
            "title": "Recon",
            "category": "workflow",
            "status": "published",
            "runbookKind": "recon",
        },
        *actions,
    ]
    return {"curatedActions": curated_actions}
