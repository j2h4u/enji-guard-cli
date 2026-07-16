import json
from pathlib import Path
from typing import cast

import pytest

import enji_guard_cli.enji_gateway.catalog_snapshot as audit_catalog_snapshot
import enji_guard_cli.enji_gateway.http as enji_api
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue


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
