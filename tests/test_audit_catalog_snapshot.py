import json
from pathlib import Path
from typing import cast

import pytest

from enji_guard_cli.audit import catalog_observation
from enji_guard_cli.audit.catalog_observation import AuditCatalogObserver
from enji_guard_cli.audit.ports import AuditCatalogAction, AuditCatalogResult


def test_observer_baselines_and_detects_published_audit_changes(tmp_path: Path) -> None:
    state_file = tmp_path / "state" / "audit-catalog.json"
    observer = AuditCatalogObserver(state_file)
    initial = _catalog(
        _action("audit.security", title="Security", metric_group="vulns"),
        _action("audit.tests", title="Tests", metric_group="tests"),
    )
    current = _catalog(
        _action("audit.security", title="Application security", metric_group="vulns"),
        _action("audit.ai-readiness", title="AI readiness", metric_group="ai-readiness"),
    )

    assert observer.observe(initial).changes == ()
    assert state_file.exists()
    assert state_file.stat().st_mode & 0o777 == 0o600
    snapshot = cast(dict[str, object], json.loads(state_file.read_text(encoding="utf-8")))
    audits = snapshot["audits"]
    assert isinstance(audits, dict)
    assert set(audits) == {"audit.security", "audit.tests"}
    assert "audit.recon" not in audits

    changes = observer.observe(current).changes
    assert [(change.kind, change.action_key) for change in changes] == [
        ("added", "audit.ai-readiness"),
        ("removed", "audit.tests"),
        ("changed", "audit.security"),
    ]
    assert changes[-1].changed_fields == ("title",)
    assert observer.observe(current).changes == ()


def test_observer_tracks_typed_catalog_results(tmp_path: Path) -> None:
    observer = AuditCatalogObserver(tmp_path / "audit-catalog.json")
    baseline = _catalog(_action("audit.security", title="Security", metric_group="vulns"))
    added = _catalog(
        _action("audit.security", title="Security", metric_group="vulns"),
        _action("audit.open-source", title="Open source", metric_group="open-source"),
    )

    observer.observe(baseline)
    assert [(change.kind, change.action_key) for change in observer.observe(AuditCatalogResult(())).changes] == [
        ("removed", "audit.security")
    ]
    assert [(change.kind, change.action_key) for change in observer.observe(added).changes] == [
        ("added", "audit.open-source"),
        ("added", "audit.security"),
    ]


def test_observer_surfaces_snapshot_persistence_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_write(_path: Path, _payload: object) -> None:
        raise OSError("snapshot persistence failed")

    monkeypatch.setattr(catalog_observation, "write_atomic_json", fail_write)

    with pytest.raises(OSError, match="snapshot persistence failed"):
        AuditCatalogObserver(tmp_path / "audit-catalog.json").observe(
            _catalog(_action("audit.security", title="Security", metric_group="vulns"))
        )


def _action(action_key: str, *, title: str, metric_group: str) -> AuditCatalogAction:
    return AuditCatalogAction(
        action_key=action_key,
        title=title,
        category="audit",
        status="published",
        metric_group=metric_group,
        runbook_kind=f"{metric_group}-audit",
    )


def _catalog(*actions: AuditCatalogAction) -> AuditCatalogResult:
    return AuditCatalogResult(
        actions=(
            AuditCatalogAction("audit.recon", "Recon", "workflow", "published", None, "recon"),
            *actions,
        )
    )
