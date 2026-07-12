from typing import cast

import pytest
from pytest import MonkeyPatch

import enji_guard_cli.core as core
from enji_guard_cli.core import AutofixSettingsUpdate


def test_list_normalizes_live_improvement_autofixes_and_excludes_pentest(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(core, "run_catalog", _catalog)
    monkeypatch.setattr(core, "_selected_repo_targets", lambda _repo, _project: [_target()])
    monkeypatch.setattr(core, "run_improvement_jobs", lambda _repo_id: {"jobs": []})

    payload = core.list_autofix_settings("j2h4u/enji-guard-cli", None)

    autofixes = cast(list[dict[str, object]], payload["autofixes"])
    assert [(row["source_audit"], row["autofix"], row["supported"]) for row in autofixes] == [
        ("audit.security", "vuln-fix", True),
        ("audit.tests", "test-writing", True),
        ("audit.dependency-hygiene", "dependency-update", True),
    ]
    assert "pentest" not in str(payload)


def test_list_reads_audit_autofixes_without_parsing_curated_actions(monkeypatch: MonkeyPatch) -> None:
    catalog = _catalog()
    catalog.pop("curatedActions")
    monkeypatch.setattr(core, "run_catalog", lambda: catalog)
    monkeypatch.setattr(core, "_selected_repo_targets", lambda _repo, _project: [_target()])
    monkeypatch.setattr(core, "run_improvement_jobs", lambda _repo_id: {"jobs": []})

    payload = core.list_autofix_settings("j2h4u/enji-guard-cli", None)

    autofixes = cast(list[dict[str, object]], payload["autofixes"])
    assert [row["autofix"] for row in autofixes] == ["vuln-fix", "test-writing", "dependency-update"]
    assert all(row["supported"] is False for row in autofixes)


def test_set_uses_full_default_job_and_preserves_existing_fields(monkeypatch: MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(core, "run_catalog", _catalog)
    monkeypatch.setattr(core, "_selected_write_repo_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(core, "run_improvement_jobs", lambda _repo_id: {"jobs": []})
    monkeypatch.setattr(
        core, "run_put_improvement_job", lambda _repo_id, _kind, job: captured.append(job) or {"job": job}
    )

    payload = core.set_autofix_settings(
        "j2h4u/enji-guard-cli", None, ["vuln-fix"], AutofixSettingsUpdate(True, None, "Asia/Almaty")
    )

    assert captured == [
        {
            "enabled": True,
            "autoFix": True,
            "autofixVariantKey": "default",
            "frequency": "workdays",
            "daysOfWeek": ["mon", "tue", "wed", "thu", "fri"],
            "scheduleTime": "09:00",
            "scheduleTimeSource": "auto",
            "timezone": "Asia/Almaty",
            "pentestMode": "off",
        }
    ]
    assert cast(list[dict[str, object]], payload["autofixes"])[0]["status"] == "changed"
    existing = {**captured[0], "kind": "vuln-fix", "binding": {"preserved": True}}
    monkeypatch.setattr(core, "run_improvement_jobs", lambda _repo_id: {"jobs": [existing]})
    monkeypatch.setattr(
        core, "run_put_improvement_job", lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected write"))
    )
    unchanged = core.set_autofix_settings(
        "j2h4u/enji-guard-cli", None, ["vuln-fix"], AutofixSettingsUpdate(True, None, "Asia/Almaty")
    )
    assert cast(list[dict[str, object]], unchanged["autofixes"])[0]["status"] == "unchanged"


def test_set_rejects_unknown_relationship_missing_enabled_and_all_with_unsupported(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(core, "run_catalog", _catalog_with_unknown)
    with pytest.raises(ValueError, match="unsupported until a relationship"):
        core.set_autofix_settings("j2h4u/enji-guard-cli", None, ["unknown"], AutofixSettingsUpdate(True, None, None))
    with pytest.raises(ValueError, match="unsupported until a relationship"):
        core.set_autofix_settings("j2h4u/enji-guard-cli", None, ["__all__"], AutofixSettingsUpdate(True, None, None))
    with pytest.raises(ValueError, match="pass --enabled on or off"):
        core.set_autofix_settings("j2h4u/enji-guard-cli", None, ["vuln-fix"], AutofixSettingsUpdate(None, None, None))


def test_disabling_absent_autofix_is_unchanged_and_enabling_requires_timezone(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(core, "run_catalog", _catalog)
    monkeypatch.setattr(core, "_selected_write_repo_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(core, "run_improvement_jobs", lambda _repo_id: {"jobs": []})
    monkeypatch.setattr(
        core, "run_put_improvement_job", lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected write"))
    )

    disabled = core.set_autofix_settings(
        "j2h4u/enji-guard-cli", None, ["vuln-fix"], AutofixSettingsUpdate(False, None, None)
    )

    assert cast(list[dict[str, object]], disabled["autofixes"])[0]["status"] == "unchanged"
    with pytest.raises(ValueError, match="pass --timezone when enabling an absent autofix"):
        core.set_autofix_settings("j2h4u/enji-guard-cli", None, ["vuln-fix"], AutofixSettingsUpdate(True, None, None))


def _target() -> dict[str, object]:
    return {
        "project_id": "project_1",
        "project_name": "Pets",
        "repo_id": "repo_1",
        "github_owner": "j2h4u",
        "github_name": "enji-guard-cli",
        "github_repo": "j2h4u/enji-guard-cli",
        "connected": True,
        "recon_done": True,
        "scores": {},
        "score_grades": {},
        "score_summary": {
            "overall_score": None,
            "overall_grade": None,
            "weakest_axis": None,
            "weakest_score": None,
            "weakest_grade": None,
        },
    }


def _catalog() -> dict[str, object]:
    actions = [
        {
            "actionKey": "audit.recon",
            "title": "Recon",
            "category": "workflow",
            "status": "published",
            "runbookKind": "recon",
        },
        {
            "actionKey": "audit.security",
            "title": "Security",
            "category": "audit",
            "status": "published",
            "runbookKind": "security",
            "metricGroup": "vulns",
        },
        {
            "actionKey": "audit.tests",
            "title": "Tests",
            "category": "audit",
            "status": "published",
            "runbookKind": "tests",
            "metricGroup": "tests",
        },
        {
            "actionKey": "audit.dependency-hygiene",
            "title": "Dependency hygiene",
            "category": "audit",
            "status": "published",
            "runbookKind": "dependency-hygiene",
            "metricGroup": "dependency-hygiene",
        },
    ]
    return {
        "curatedActions": actions,
        "auditAutofixes": [
            {
                "actionKey": "improvement.vuln-fix",
                "variantKey": "default",
                "title": "Vulnerability fixes",
                "status": "published",
            },
            {
                "actionKey": "improvement.test-writing",
                "variantKey": "default",
                "title": "Test writing",
                "status": "published",
            },
            {
                "actionKey": "improvement.dependency-update",
                "variantKey": "default",
                "title": "Dependency update",
                "status": "published",
            },
            {
                "actionKey": "improvement.pentest",
                "variantKey": "default",
                "title": "Pentest",
                "status": "published",
            },
        ],
    }


def _catalog_with_unknown() -> dict[str, object]:
    catalog = _catalog()
    autofixes = cast(list[object], catalog["auditAutofixes"])
    autofixes.append(
        {"actionKey": "improvement.unknown", "variantKey": "default", "title": "Unknown", "status": "published"}
    )
    return catalog
