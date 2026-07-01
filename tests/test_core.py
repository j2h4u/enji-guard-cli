from typing import cast

import pytest
from pytest import MonkeyPatch

import enji_guard_cli.core as core
from enji_guard_cli.audits import (
    AUDITS,
    REPORT_AUDIT_ALIASES,
    AuditAlias,
    ReportAuditAlias,
    audit_catalog,
    audit_payload,
    resolve_audit,
)
from enji_guard_cli.core import (
    OPERATION_SPECS,
    EmailPreferenceUpdate,
    OperationName,
    ReportWaitOptions,
    ScheduleSettingsUpdate,
    operation_catalog,
    resolve_operation,
    resolve_operation_spec,
)
from enji_guard_cli.errors import EnjiApiError


def test_operation_catalog_has_unique_operation_and_mcp_tool_names() -> None:
    catalog = operation_catalog()

    assert [entry["name"] for entry in catalog] == [spec.name.value for spec in OPERATION_SPECS]
    assert len({entry["name"] for entry in catalog}) == len(catalog)
    assert len({entry["mcp_tool"] for entry in catalog}) == len(catalog)


def test_operation_catalog_includes_catalog_access_reports_and_auth_specs() -> None:
    assert operation_catalog() == [
        {
            "name": OperationName.CATALOG_AUDITS.value,
            "cli_command": "catalog audits",
            "mcp_tool": "enji_catalog_audits",
            "summary": "List the canonical Enji Guard audit catalog.",
        },
        {
            "name": OperationName.CATALOG_AUDIT.value,
            "cli_command": "catalog audit",
            "mcp_tool": "enji_catalog_audit",
            "summary": "Resolve one canonical Enji Guard audit alias.",
        },
        {
            "name": OperationName.ACCESS.value,
            "cli_command": "access",
            "mcp_tool": "enji_access",
            "summary": "Return Enji Guard plan, limits, and schedule access metadata.",
        },
        {
            "name": OperationName.REPORTS_LIST.value,
            "cli_command": None,
            "mcp_tool": "enji_reports_list",
            "summary": "List compact Enji Guard report inventory for MCP.",
        },
        {
            "name": OperationName.AUTH_STATUS.value,
            "cli_command": "auth status",
            "mcp_tool": "enji_auth_status",
            "summary": "Report whether stored Enji Guard credentials are authenticated.",
        },
    ]


def test_resolve_operation_returns_new_access_and_reports_specs() -> None:
    assert resolve_operation(OperationName.ACCESS) == {
        "name": "access",
        "cli_command": "access",
        "mcp_tool": "enji_access",
        "summary": "Return Enji Guard plan, limits, and schedule access metadata.",
    }
    assert resolve_operation(OperationName.REPORTS_LIST) == {
        "name": "reports_list",
        "cli_command": None,
        "mcp_tool": "enji_reports_list",
        "summary": "List compact Enji Guard report inventory for MCP.",
    }


def test_operation_specs_are_executable_bindings() -> None:
    assert resolve_operation_spec(OperationName.CATALOG_AUDITS).execute() == audit_catalog()
    assert resolve_operation_spec(OperationName.CATALOG_AUDIT).execute(AuditAlias.DEPS) == audit_payload(
        resolve_audit(AuditAlias.DEPS)
    )
    assert callable(resolve_operation_spec(OperationName.ACCESS).execute)
    assert callable(resolve_operation_spec(OperationName.REPORTS_LIST).execute)
    assert callable(resolve_operation_spec(OperationName.AUTH_STATUS).execute)


def test_audit_catalog_is_derived_from_canonical_audit_definitions() -> None:
    assert len(audit_catalog()) == len(AUDITS)
    assert audit_payload(resolve_audit(AuditAlias.RECON)) == {
        "alias": "recon",
        "label": "Recon",
        "route_slug": None,
        "job_kind": None,
        "action_key": "audit.recon",
    }


def test_report_audit_alias_enum_matches_report_registry() -> None:
    assert tuple(AuditAlias(alias.value) for alias in ReportAuditAlias) == REPORT_AUDIT_ALIASES


def test_set_schedule_normalizes_json_payload(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_put_improvement_job(repo_id: str, job_kind: str, payload: object) -> dict[str, object]:
        captured["repo_id"] = repo_id
        captured["job_kind"] = job_kind
        captured["payload"] = payload
        return {"job": payload}

    monkeypatch.setattr(core, "run_put_improvement_job", fake_put_improvement_job)

    payload = core.set_schedule(
        "repo_1",
        AuditAlias.SECURITY,
        {
            "enabled": True,
            "autoFix": False,
            "frequency": "weekly",
            "daysOfWeek": ["mon", "wed"],
            "timezone": "UTC",
            "nested": {"limit": 1, "note": None},
        },
    )

    assert payload["job"] == captured["payload"]
    assert captured["repo_id"] == "repo_1"
    assert captured["job_kind"] == "vuln-audit"


def test_report_status_derives_ready_and_missing_reports_from_task_links(monkeypatch: MonkeyPatch) -> None:
    def fake_task_links(repo_id: str) -> dict[str, object]:
        assert repo_id == "repo_1"
        return {
            "links": [
                {
                    "actionKey": "audit.security",
                    "artifactSchemaName": "upfront.audit.summary",
                    "fleetTaskId": "task_security",
                    "createdAt": "2026-06-29T12:00:00Z",
                },
                {
                    "actionKey": "audit.recon",
                    "artifactSchemaName": "other.schema",
                    "fleetTaskId": "task_recon",
                },
            ]
        }

    monkeypatch.setattr(core, "run_repo_task_links", fake_task_links)
    monkeypatch.setattr(core, "run_repo_active_runs", lambda repo_id: {"activeRuns": []})
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {
            "state": {
                "currentHeadSha": "head_2",
                "actions": {"audit.security": {"lastAuditedHeadSha": "head_2"}},
            }
        },
    )

    payload = core.report_status("repo_1")

    assert payload["complete"] is False
    assert payload["ready"] == ["security"]
    assert payload["running"] == []
    assert payload["missing"] == ["ai-readiness", "tests", "tech-health", "deps", "cognitive-debt", "dead-code"]
    assert payload["reports"][0] == {
        "audit": "security",
        "label": "Security",
        "action_key": "audit.security",
        "route_slug": "vulns",
        "state": "ready",
        "ready": True,
        "running": False,
        "fleet_task_id": "task_security",
        "created_at": "2026-06-29T12:00:00Z",
        "started_at": None,
        "completed_at": None,
        "run_status": None,
        "current_head_sha": "head_2",
        "last_audited_head_sha": "head_2",
        "out_of_date": False,
    }


def test_report_status_marks_started_report_runs_as_running(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        core,
        "run_repo_task_links",
        lambda repo_id: {
            "links": [
                {
                    "actionKey": "audit.security",
                    "artifactSchemaName": "upfront.audit.summary",
                    "fleetTaskId": "task_security",
                    "createdAt": "2026-06-29T12:00:00Z",
                }
            ]
        },
    )
    monkeypatch.setattr(
        core,
        "run_repo_active_runs",
        lambda repo_id: {
            "activeRuns": [
                {
                    "actionKey": "audit.security",
                    "fleetTaskId": "task_security",
                    "createdAt": "2026-06-29T12:00:00Z",
                    "startedAt": "2026-06-29T12:00:01Z",
                    "completedAt": None,
                    "status": "in_progress",
                }
            ]
        },
    )
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {
            "state": {
                "currentHeadSha": "head_2",
                "actions": {
                    "audit.security": {"lastAuditedHeadSha": "head_1"},
                    "audit.tests": {"lastAuditedHeadSha": "head_1"},
                    "audit.dead-code": {"lastAuditedHeadSha": "head_1"},
                },
            }
        },
    )
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {
            "state": {
                "currentHeadSha": "head_2",
                "actions": {"audit.security": {"lastAuditedHeadSha": "head_1"}},
            }
        },
    )

    payload = core.report_status("repo_1")

    assert payload["complete"] is False
    assert payload["ready"] == []
    assert payload["running"] == ["security"]
    assert payload["missing"] == ["ai-readiness", "tests", "tech-health", "deps", "cognitive-debt", "dead-code"]
    assert payload["reports"][0] == {
        "audit": "security",
        "label": "Security",
        "action_key": "audit.security",
        "route_slug": "vulns",
        "state": "running",
        "ready": False,
        "running": True,
        "fleet_task_id": "task_security",
        "created_at": "2026-06-29T12:00:00Z",
        "started_at": "2026-06-29T12:00:01Z",
        "completed_at": None,
        "run_status": "in_progress",
        "current_head_sha": "head_2",
        "last_audited_head_sha": "head_1",
        "out_of_date": True,
    }


def test_repo_status_combines_active_runs_rerun_state_and_report_status(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(core, "run_repo_active_runs", lambda repo_id: {"activeRuns": []})
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {
            "state": {
                "currentHeadSha": "head_2",
                "actions": {"audit.security": {"lastAuditedHeadSha": "head_1"}},
            }
        },
    )
    monkeypatch.setattr(core, "run_repo_task_links", lambda repo_id: {"links": []})

    payload = core.repo_status("repo_1")

    assert payload["repo_id"] == "repo_1"
    assert payload["active_run_count"] == 0
    assert payload["active_runs"] == []
    assert payload["current_head_sha"] == "head_2"
    assert payload["rerun_state"] == {
        "state": {
            "currentHeadSha": "head_2",
            "actions": {"audit.security": {"lastAuditedHeadSha": "head_1"}},
        }
    }
    assert payload["reports"]["missing"] == [
        "security",
        "ai-readiness",
        "tests",
        "tech-health",
        "deps",
        "cognitive-debt",
        "dead-code",
    ]
    assert payload["reports"]["reports"][0]["out_of_date"] is True


def test_repo_status_all_summarizes_projects_repos_runs_and_reports(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "pets"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": False,
                    "scores": {"tech-health": 49, "dead-code": 90, "metadata": {"source": "fixture"}},
                },
                {
                    "id": "repo_2",
                    "githubOwner": "j2h4u",
                    "githubName": "watchdirs",
                    "connected": True,
                    "reconDone": True,
                    "scores": {"tests": 76, "flag": True},
                },
            ],
        },
    )
    monkeypatch.setattr(
        core,
        "run_repo_active_runs",
        lambda repo_id: (
            {
                "activeRuns": [
                    {
                        "actionKey": "audit.recon",
                        "completedAt": None,
                        "status": "in_progress",
                    }
                ]
            }
            if repo_id == "repo_1"
            else {
                "activeRuns": [
                    {
                        "actionKey": "audit.security",
                        "completedAt": "2026-06-29T12:00:00Z",
                        "status": "completed",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(core, "run_repo_task_links", lambda repo_id: {"links": []})
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {"state": {"currentHeadSha": f"{repo_id}_head", "actions": {}}},
    )

    payload = core.repo_status_all(None)

    assert payload["summary"] == {
        "project_count": 1,
        "repo_count": 2,
        "connected_repo_count": 2,
        "active_run_count": 1,
        "recon_done_count": 1,
        "report_complete_count": 0,
    }
    assert payload["projects"][0]["project_name"] == "pets"
    assert payload["projects"][0]["repos"][0]["github_repo"] == "j2h4u/enji-guard-cli"
    assert payload["projects"][0]["repos"][0]["active_run_count"] == 1
    assert payload["projects"][0]["repos"][0]["current_head_sha"] == "repo_1_head"
    assert payload["projects"][0]["repos"][0]["scores"] == {
        "tech-health": 49,
        "dead-code": 90,
        "metadata": {"source": "fixture"},
    }
    assert payload["projects"][0]["repos"][0]["score_grades"] == {
        "tech-health": "poor",
        "dead-code": "excellent",
    }
    assert payload["projects"][0]["repos"][0]["score_summary"] == {
        "overall_score": 69.5,
        "overall_grade": "fair",
        "weakest_axis": "tech-health",
        "weakest_score": 49.0,
        "weakest_grade": "poor",
    }
    assert payload["projects"][0]["repos"][1]["reports"]["complete"] is False
    assert payload["projects"][0]["repos"][1]["score_grades"] == {"tests": "good"}


def test_project_admin_operations_resolve_selectors_and_validate_names(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1", "name": "Pets"}]})

    def fake_create(name: str) -> dict[str, object]:
        captured["created_name"] = name
        return {"project": {"id": "project_2", "name": name}}

    def fake_rename(project_id: str, name: str) -> dict[str, object]:
        captured["renamed_project_id"] = project_id
        captured["renamed_name"] = name
        return {"project": {"id": project_id, "name": name}}

    def fake_delete(project_id: str) -> None:
        captured["deleted_project_id"] = project_id

    monkeypatch.setattr(core, "run_create_project", fake_create)
    monkeypatch.setattr(core, "run_rename_project", fake_rename)
    monkeypatch.setattr(core, "run_delete_project", fake_delete)

    assert core.create_project(" Friends ") == {
        "project_name": "Friends",
        "response": {"project": {"id": "project_2", "name": "Friends"}},
    }
    assert core.rename_project("pets", " Work ") == {
        "project_id": "project_1",
        "project_name": "Work",
        "response": {"project": {"id": "project_1", "name": "Work"}},
    }
    assert core.delete_project("PETS") == {"project_id": "project_1", "deleted": True}
    assert captured == {
        "created_name": "Friends",
        "renamed_project_id": "project_1",
        "renamed_name": "Work",
        "deleted_project_id": "project_1",
    }

    with pytest.raises(ValueError, match="project name must not be empty"):
        core.create_project(" ")


def test_move_repo_resolves_source_and_target_and_preflights_before_transfer(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        core,
        "run_projects",
        lambda: {
            "projects": [
                {"id": "project_1", "name": "Pets"},
                {"id": "project_2", "name": "Work"},
            ]
        },
    )
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets" if project_id == "project_1" else "Work"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": True,
                }
            ]
            if project_id == "project_1"
            else [],
        },
    )

    def fake_preflight(source_project_id: str, repo_id: str, target_project_id: str) -> dict[str, object]:
        captured["preflight"] = (source_project_id, repo_id, target_project_id)
        return {"ok": True, "scheduleReplacements": {"security": {"scheduleId": "schedule_1"}}}

    def fake_move(request: core.RepoTransfer) -> dict[str, object]:
        captured["move"] = (request.source_project_id, request.repo_id, request.target_project_id)
        captured["schedule_replacements"] = request.schedule_replacements
        return {"repo": {"id": request.repo_id, "projectId": request.target_project_id}}

    monkeypatch.setattr(core, "run_preflight_repo_move", fake_preflight)
    monkeypatch.setattr(core, "run_move_repo", fake_move)

    payload = core.move_repo("j2h4u/enji-guard-cli", "Pets", "Work")

    assert payload["source_project_id"] == "project_1"
    assert payload["target_project_id"] == "project_2"
    assert payload["preflight"] == {"ok": True, "scheduleReplacements": {"security": {"scheduleId": "schedule_1"}}}
    assert payload["response"] == {"repo": {"id": "repo_1", "projectId": "project_2"}}
    assert captured == {
        "preflight": ("project_1", "repo_1", "project_2"),
        "move": ("project_1", "repo_1", "project_2"),
        "schedule_replacements": {"security": {"scheduleId": "schedule_1"}},
    }


def test_move_repo_rejects_same_source_and_target_project(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1", "name": "Pets"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="repo is already in target project"):
        core.move_repo("j2h4u/enji-guard-cli", "Pets", "Pets")


def test_list_project_inventory_can_sort_repos_by_weakest_score(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_good",
                    "githubOwner": "j2h4u",
                    "githubName": "good",
                    "connected": True,
                    "reconDone": True,
                    "scores": {"tests": 92, "vulns": 88},
                },
                {
                    "id": "repo_bad",
                    "githubOwner": "j2h4u",
                    "githubName": "bad",
                    "connected": True,
                    "reconDone": True,
                    "scores": {"tests": 75, "tech-health": 28},
                },
                {
                    "id": "repo_unknown",
                    "githubOwner": "j2h4u",
                    "githubName": "unknown",
                    "connected": True,
                    "reconDone": True,
                    "scores": {},
                },
            ],
        },
    )

    payload = core.list_project_inventory(None, sort="weakest")

    assert [repo["repo_id"] for repo in payload["projects"][0]["repos"]] == [
        "repo_bad",
        "repo_good",
        "repo_unknown",
    ]


def test_list_project_inventory_can_sort_repos_by_latest_report(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_old",
                    "githubOwner": "j2h4u",
                    "githubName": "old",
                    "connected": True,
                    "reconDone": True,
                    "scores": {},
                },
                {
                    "id": "repo_new",
                    "githubOwner": "j2h4u",
                    "githubName": "new",
                    "connected": True,
                    "reconDone": True,
                    "scores": {},
                },
                {
                    "id": "repo_missing",
                    "githubOwner": "j2h4u",
                    "githubName": "missing",
                    "connected": True,
                    "reconDone": True,
                    "scores": {},
                },
            ],
        },
    )
    monkeypatch.setattr(core, "run_repo_active_runs", lambda repo_id: {"activeRuns": []})
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {"state": {"currentHeadSha": f"{repo_id}_head", "actions": {}}},
    )
    monkeypatch.setattr(
        core,
        "run_repo_task_links",
        lambda repo_id: {
            "links": {
                "repo_new": [
                    {
                        "actionKey": "audit.security",
                        "artifactSchemaName": "upfront.audit.summary",
                        "completedAt": "2026-06-30T12:00:00Z",
                    }
                ],
                "repo_old": [
                    {
                        "actionKey": "audit.security",
                        "artifactSchemaName": "upfront.audit.summary",
                        "createdAt": "2026-06-29T12:00:00Z",
                    }
                ],
            }.get(repo_id, [])
        },
    )

    payload = core.list_project_inventory(None, sort="latest-report")

    repos = payload["projects"][0]["repos"]
    assert [repo["repo_id"] for repo in repos] == ["repo_new", "repo_old", "repo_missing"]
    assert repos[0]["last_report_at"] == "2026-06-30T12:00:00Z"
    assert repos[1]["last_report_at"] == "2026-06-29T12:00:00Z"
    assert repos[2]["last_report_at"] is None


def test_resolve_repo_accepts_project_name_and_owner_repo_selector(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        core,
        "run_projects",
        lambda: {"projects": [{"id": "project_1", "name": "Pets"}]},
    )
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": True,
                }
            ],
        },
    )

    payload = core.resolve_repo("j2h4u/enji-guard-cli", "Pets")

    assert payload["resolved"] is True
    assert payload["matches"][0]["project_id"] == "project_1"
    assert payload["matches"][0]["repo_id"] == "repo_1"
    assert payload["matches"][0]["github_repo"] == "j2h4u/enji-guard-cli"


def test_resolve_repo_reports_ambiguous_owner_repo_candidates(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        core,
        "run_projects",
        lambda: {
            "projects": [
                {"id": "project_1", "name": "Pets"},
                {"id": "project_2", "name": "Ops"},
            ]
        },
    )
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {
            "state": {
                "currentHeadSha": "head_2",
                "actions": {
                    "audit.security": {"lastAuditedHeadSha": "head_1"},
                    "audit.tests": {"lastAuditedHeadSha": "head_1"},
                    "audit.dead-code": {"lastAuditedHeadSha": "head_1"},
                },
            }
        },
    )
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets" if project_id == "project_1" else "Ops"},
            "repos": [
                {
                    "id": f"repo_{project_id}",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": True,
                }
            ],
        },
    )

    payload = core.resolve_repo("j2h4u/enji-guard-cli", None)

    assert payload["resolved"] is False
    assert [match["project_id"] for match in payload["matches"]] == ["project_1", "project_2"]


def test_start_recon_rejects_ambiguous_owner_repo_selector(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        core,
        "run_projects",
        lambda: {
            "projects": [
                {"id": "project_1", "name": "Pets"},
                {"id": "project_2", "name": "Ops"},
            ]
        },
    )
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets" if project_id == "project_1" else "Ops"},
            "repos": [
                {
                    "id": f"repo_{project_id}",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": True,
                }
            ],
        },
    )

    with pytest.raises(EnjiApiError) as exc_info:
        core.start_recon("j2h4u/enji-guard-cli", None)

    assert exc_info.value.code == "BAD_SELECTOR"
    assert "ambiguous" in exc_info.value.message


def test_runtime_status_can_filter_one_repo_by_selector(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1", "name": "Pets"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": True,
                },
                {
                    "id": "repo_2",
                    "githubOwner": "j2h4u",
                    "githubName": "watchdirs",
                    "connected": True,
                    "reconDone": False,
                    "scores": {"vulns": 88, "tech-health": 49},
                },
            ],
        },
    )
    monkeypatch.setattr(core, "run_repo_active_runs", lambda repo_id: {"activeRuns": []})
    monkeypatch.setattr(core, "run_repo_task_links", lambda repo_id: {"links": []})
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {"state": {"currentHeadSha": "watchdirs_head", "actions": {}}},
    )

    payload = core.runtime_status("j2h4u/watchdirs", None)

    assert payload["summary"]["repo_count"] == 1
    assert payload["projects"][0]["repos"][0]["repo_id"] == "repo_2"
    assert payload["projects"][0]["repos"][0]["current_head_sha"] == "watchdirs_head"
    assert payload["projects"][0]["repos"][0]["score_summary"] == {
        "overall_score": 68.5,
        "overall_grade": "fair",
        "weakest_axis": "tech-health",
        "weakest_score": 49.0,
        "weakest_grade": "poor",
    }


def test_start_recon_resolves_repo_and_project(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1", "name": "Pets"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": False,
                }
            ],
        },
    )

    def fake_start(repo_id: str, project_id: str, audit: AuditAlias) -> dict[str, object]:
        captured["repo_id"] = repo_id
        captured["project_id"] = project_id
        captured["audit"] = audit.value
        return {"task": {"id": "task_1"}}

    monkeypatch.setattr(core, "start_audit", fake_start)

    payload = core.start_recon("j2h4u/enji-guard-cli", "Pets")

    target = payload["target"]
    assert isinstance(target, dict)
    assert target["repo_id"] == "repo_1"
    assert payload["task"] == {"id": "task_1"}
    assert captured == {"repo_id": "repo_1", "project_id": "project_1", "audit": "recon"}


def test_start_report_audits_selects_all_report_audits(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        core,
        "_resolve_single_repo_target",
        lambda repo, project: {
            "project_id": "project_1",
            "project_name": "Pets",
            "repo_id": "repo_1",
            "github_repo": "j2h4u/enji-guard-cli",
        },
    )

    def fake_start_report_audits_for_target(
        repo_id: str, project_id: str, audits: list[AuditAlias]
    ) -> dict[str, object]:
        captured["repo_id"] = repo_id
        captured["project_id"] = project_id
        captured["audits"] = [audit.value for audit in audits]
        return {"runs": []}

    monkeypatch.setattr(core, "_start_report_audits_for_target", fake_start_report_audits_for_target)

    payload = core.start_report_audits("j2h4u/enji-guard-cli", "Pets", [], all_reports=True)

    assert payload["target"] == {
        "project_id": "project_1",
        "project_name": "Pets",
        "repo_id": "repo_1",
        "github_repo": "j2h4u/enji-guard-cli",
    }
    assert payload["runs"] == []
    assert captured == {
        "repo_id": "repo_1",
        "project_id": "project_1",
        "audits": ["security", "ai-readiness", "tests", "tech-health", "deps", "cognitive-debt", "dead-code"],
    }


def test_start_all_report_audits_skips_already_running_audits(monkeypatch: MonkeyPatch) -> None:
    captured_action_keys: list[str] = []
    monkeypatch.setattr(
        core,
        "run_repo_active_runs",
        lambda repo_id: {
            "activeRuns": [
                {
                    "actionKey": "audit.security",
                    "fleetTaskId": "task_security",
                    "status": "pending",
                    "completedAt": None,
                },
                {
                    "task": {"actionKey": "audit.tests"},
                    "fleetTaskId": "task_tests",
                    "status": "in_progress",
                    "completedAt": None,
                },
                {
                    "actionKey": "audit.dead-code",
                    "fleetTaskId": "task_dead_code_done",
                    "status": "completed",
                    "completedAt": "2026-06-29T12:00:00Z",
                },
            ]
        },
    )
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {
            "state": {
                "currentHeadSha": "head_2",
                "actions": {
                    "audit.security": {"lastAuditedHeadSha": "head_1"},
                    "audit.tests": {"lastAuditedHeadSha": "head_1"},
                    "audit.dead-code": {"lastAuditedHeadSha": "head_1"},
                },
            }
        },
    )
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {"project": {"id": project_id, "name": "Pets"}},
    )
    monkeypatch.setattr(core, "run_catalog", lambda: {"curatedActions": []})
    monkeypatch.setattr(
        core,
        "_audit_run_task_body_from_context",
        lambda project_id, repo_id, action_key, project, catalog: {"title": "Run audit"},
    )

    def fake_start(request: core.AuditRunCreate) -> dict[str, object]:
        captured_action_keys.append(request.action_key)
        return {"task": {"id": f"task_{request.action_key}", "status": "pending"}}

    monkeypatch.setattr(core, "run_start_audit_run", fake_start)

    payload = core._start_report_audits_for_target(
        "repo_1",
        "project_1",
        [AuditAlias.SECURITY, AuditAlias.AI_READINESS, AuditAlias.TESTS, AuditAlias.DEAD_CODE],
    )

    assert captured_action_keys == ["audit.ai-readiness", "audit.dead-code"]
    assert [run["audit"] for run in payload["runs"]] == ["ai-readiness", "dead-code"]
    assert payload["skipped"] == [
        {
            "audit": "security",
            "action_key": "audit.security",
            "reason": "already_running",
            "active_runs": [
                {
                    "actionKey": "audit.security",
                    "fleetTaskId": "task_security",
                    "status": "pending",
                    "completedAt": None,
                }
            ],
            "current_head_sha": "head_2",
            "last_audited_head_sha": "head_1",
        },
        {
            "audit": "tests",
            "action_key": "audit.tests",
            "reason": "already_running",
            "active_runs": [
                {
                    "task": {"actionKey": "audit.tests"},
                    "fleetTaskId": "task_tests",
                    "status": "in_progress",
                    "completedAt": None,
                }
            ],
            "current_head_sha": "head_2",
            "last_audited_head_sha": "head_1",
        },
    ]


def test_start_report_audits_skips_up_to_date_audits(monkeypatch: MonkeyPatch) -> None:
    def fail_start(request: core.AuditRunCreate) -> object:
        raise AssertionError("up-to-date audit should not be started")

    monkeypatch.setattr(core, "run_repo_active_runs", lambda repo_id: {"activeRuns": []})
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {
            "state": {
                "currentHeadSha": "head_2",
                "actions": {"audit.security": {"lastAuditedHeadSha": "head_2"}},
            }
        },
    )
    monkeypatch.setattr(core, "run_project_detail", lambda project_id: {"project": {"id": project_id}})
    monkeypatch.setattr(core, "run_catalog", lambda: {"curatedActions": []})
    monkeypatch.setattr(core, "run_start_audit_run", fail_start)

    payload = core._start_report_audits_for_target("repo_1", "project_1", [AuditAlias.SECURITY])

    assert payload == {
        "runs": [],
        "skipped": [
            {
                "audit": "security",
                "action_key": "audit.security",
                "reason": "up_to_date",
                "active_runs": [],
                "current_head_sha": "head_2",
                "last_audited_head_sha": "head_2",
            }
        ],
    }


@pytest.mark.parametrize(
    ("audits", "all_reports", "message"),
    [
        ([], False, "pass at least one report audit or --all"),
        ([AuditAlias.SECURITY], True, "pass report audits or --all, not both"),
        ([AuditAlias.RECON], False, "recon is not a report audit"),
    ],
)
def test_start_report_audits_rejects_invalid_selection(
    monkeypatch: MonkeyPatch,
    audits: list[AuditAlias],
    all_reports: bool,
    message: str,
) -> None:
    monkeypatch.setattr(
        core,
        "_resolve_single_repo_target",
        lambda repo, project: {
            "project_id": "project_1",
            "project_name": "Pets",
            "repo_id": "repo_1",
            "github_repo": "j2h4u/enji-guard-cli",
        },
    )

    with pytest.raises(ValueError, match=message):
        core.start_report_audits("j2h4u/enji-guard-cli", None, audits, all_reports=all_reports)


def test_read_reports_for_repo_defaults_to_ready_reports(monkeypatch: MonkeyPatch) -> None:
    captured_audits: list[str] = []
    monkeypatch.setattr(
        core,
        "_resolve_single_repo_target",
        lambda repo, project: {
            "project_id": "project_1",
            "project_name": "Pets",
            "repo_id": "repo_1",
            "github_owner": "j2h4u",
            "github_name": "enji-guard-cli",
            "github_repo": "j2h4u/enji-guard-cli",
            "connected": True,
            "recon_done": True,
        },
    )
    monkeypatch.setattr(
        core,
        "report_status",
        lambda repo_id: {
            "repo_id": repo_id,
            "current_head_sha": "head_2",
            "complete": False,
            "ready": ["security", "tests"],
            "running": ["deps"],
            "missing": ["dead-code"],
            "reports": [],
        },
    )
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {
            "state": {
                "currentHeadSha": "head_2",
                "actions": {
                    "audit.security": {"lastAuditedHeadSha": "head_2"},
                    "audit.tests": {"lastAuditedHeadSha": "head_1"},
                },
            }
        },
    )

    def fake_show_report(repo_id: str, audit: AuditAlias) -> dict[str, object]:
        captured_audits.append(audit.value)
        return {"snapshot": {"content": {"report": f"# {audit.value}"}}}

    monkeypatch.setattr(core, "show_report", fake_show_report)

    payload = core.read_reports_for_repo("j2h4u/enji-guard-cli", "Pets", [], all_reports=False)

    target = payload["target"]
    reports = payload["reports"]
    assert isinstance(target, dict)
    assert target["repo_id"] == "repo_1"
    assert reports == [
        {
            "audit": "security",
            "current_head_sha": "head_2",
            "last_audited_head_sha": "head_2",
            "out_of_date": False,
            "snapshot": {"content": {"report": "# security"}},
        },
        {
            "audit": "tests",
            "current_head_sha": "head_2",
            "last_audited_head_sha": "head_1",
            "out_of_date": True,
            "snapshot": {"content": {"report": "# tests"}},
        },
    ]
    assert captured_audits == ["security", "tests"]


def test_read_reports_for_repo_can_read_all_report_audits(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        core,
        "_resolve_single_repo_target",
        lambda repo, project: {
            "project_id": "project_1",
            "project_name": "Pets",
            "repo_id": "repo_1",
            "github_owner": "j2h4u",
            "github_name": "enji-guard-cli",
            "github_repo": "j2h4u/enji-guard-cli",
            "connected": True,
            "recon_done": True,
        },
    )
    monkeypatch.setattr(core, "show_report", lambda repo_id, audit: {"snapshot": {"content": {"report": audit.value}}})
    monkeypatch.setattr(
        core,
        "run_repo_audit_rerun_state",
        lambda repo_id: {"state": {"currentHeadSha": "head_2", "actions": {}}},
    )

    payload = core.read_reports_for_repo("j2h4u/enji-guard-cli", None, [], all_reports=True)

    reports = payload["reports"]
    assert isinstance(reports, list)
    assert [report["audit"] for report in reports if isinstance(report, dict)] == [
        "security",
        "ai-readiness",
        "tests",
        "tech-health",
        "deps",
        "cognitive-debt",
        "dead-code",
    ]
    assert all(report.get("current_head_sha") == "head_2" for report in reports if isinstance(report, dict))


def test_set_email_preferences_fans_out_over_project_repos_and_report_audits(monkeypatch: MonkeyPatch) -> None:
    captured: list[tuple[str, str, object]] = []
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1", "name": "Pets"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": True,
                },
                {
                    "id": "repo_2",
                    "githubOwner": "j2h4u",
                    "githubName": "watchdirs",
                    "connected": True,
                    "reconDone": True,
                },
            ],
        },
    )

    def fake_put(repo_id: str, action_key: str, patch: object) -> dict[str, object]:
        captured.append((repo_id, action_key, patch))
        return {"resolved": {"manualRunCompletion": True, "scheduledRunCompletion": False}}

    monkeypatch.setattr(core, "run_put_audit_email_preferences", fake_put)

    payload = core.set_email_preferences(None, "Pets", EmailPreferenceUpdate(None, False), all_repos=True)

    preferences = payload["preferences"]
    assert isinstance(preferences, list)
    assert payload["summary"] == {"repo_count": 2, "audit_count": 14}
    assert len(captured) == 14
    assert captured[0] == ("repo_1", "audit.security", {"scheduledRunCompletion": False})
    assert captured[-1] == ("repo_2", "audit.dead-code", {"scheduledRunCompletion": False})
    assert preferences[0] == {
        "project_id": "project_1",
        "project_name": "Pets",
        "repo_id": "repo_1",
        "github_repo": "j2h4u/enji-guard-cli",
        "audit": "security",
        "action_key": "audit.security",
        "manual_run_completion": True,
        "scheduled_run_completion": False,
    }


def test_set_email_preferences_rejects_empty_patch() -> None:
    with pytest.raises(ValueError, match="pass --manual or --scheduled"):
        core.set_email_preferences("j2h4u/enji-guard-cli", None, EmailPreferenceUpdate(None, None))


def test_list_schedule_settings_fans_out_over_project_repos_and_report_audits(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        core,
        "run_projects",
        lambda: {"projects": [{"id": "project_1", "name": "Pets"}]},
    )
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": True,
                }
            ],
        },
    )
    monkeypatch.setattr(
        core,
        "list_schedules",
        lambda repo_id: {
            "jobs": [
                {
                    "kind": "vuln-audit",
                    "enabled": True,
                    "autoFix": True,
                    "autofixVariantKey": "strict",
                    "frequency": "weekly-2x",
                    "daysOfWeek": ["mon", "thu"],
                    "scheduleTimeSource": "user",
                    "scheduleTime": "09:30",
                    "timezone": "Asia/Almaty",
                }
            ]
        },
    )

    payload = core.list_schedule_settings(None, "Pets")

    schedules = cast(list[dict[str, object]], payload["schedules"])
    assert payload["summary"] == {
        "repo_count": 1,
        "audit_count": 7,
        "enabled_count": 1,
        "changed_count": 0,
        "unchanged_count": 0,
    }
    assert schedules[0] == {
        "project_id": "project_1",
        "project_name": "Pets",
        "repo_id": "repo_1",
        "github_repo": "j2h4u/enji-guard-cli",
        "audit": "security",
        "job_kind": "vuln-audit",
        "configured": True,
        "enabled": True,
        "frequency": "weekly-2x",
        "days_of_week": ["mon", "thu"],
        "schedule_time": "09:30",
        "schedule_time_source": "user",
        "timezone": "Asia/Almaty",
        "auto_fix": True,
    }
    assert schedules[1]["audit"] == "ai-readiness"
    assert schedules[1]["configured"] is False


def test_set_schedule_settings_updates_project_repos_and_report_audits(monkeypatch: MonkeyPatch) -> None:
    captured: list[tuple[str, str, object]] = []
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1", "name": "Pets"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": True,
                }
            ],
        },
    )
    monkeypatch.setattr(core, "list_schedules", lambda repo_id: {"jobs": []})

    def fake_put(repo_id: str, job_kind: str, payload: object) -> dict[str, object]:
        captured.append((repo_id, job_kind, payload))
        return {"job": payload}

    monkeypatch.setattr(core, "run_put_improvement_job", fake_put)

    payload = core.set_schedule_settings(
        None,
        "Pets",
        ScheduleSettingsUpdate(
            enabled=True,
            frequency="weekly-2x",
            days_of_week=["mon", "thu"],
            schedule_time="09:30",
            timezone="Asia/Almaty",
        ),
        all_repos=True,
    )

    assert payload["summary"] == {
        "repo_count": 1,
        "audit_count": 7,
        "enabled_count": 7,
        "changed_count": 7,
        "unchanged_count": 0,
    }
    assert len(captured) == 7
    assert captured[0] == (
        "repo_1",
        "vuln-audit",
        {
            "enabled": True,
            "autoFix": False,
            "autofixVariantKey": "default",
            "frequency": "weekly-2x",
            "daysOfWeek": ["mon", "thu"],
            "scheduleTimeSource": "user",
            "timezone": "Asia/Almaty",
            "scheduleTime": "09:30",
        },
    )


def test_set_schedule_settings_skips_unchanged_existing_jobs(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        core,
        "_selected_repo_targets",
        lambda repo, project: [
            {
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
        ],
    )
    monkeypatch.setattr(
        core,
        "list_schedules",
        lambda repo_id: {
            "jobs": [
                {
                    "kind": "vuln-audit",
                    "enabled": False,
                    "autoFix": False,
                    "autofixVariantKey": "default",
                    "frequency": "weekly",
                    "daysOfWeek": ["mon"],
                    "scheduleTimeSource": "auto",
                    "timezone": "UTC",
                }
            ]
        },
    )

    def fail_put(repo_id: str, job_kind: str, payload: object) -> object:
        raise AssertionError("unchanged schedule should not be written")

    monkeypatch.setattr(core, "run_put_improvement_job", fail_put)

    payload = core.set_schedule_settings(
        "j2h4u/enji-guard-cli",
        None,
        ScheduleSettingsUpdate(
            enabled=False,
            frequency=None,
            days_of_week=None,
            schedule_time=None,
            timezone=None,
        ),
    )

    schedules = cast(list[dict[str, object]], payload["schedules"])
    summary = cast(dict[str, object], payload["summary"])
    assert summary["changed_count"] == 0
    assert summary["unchanged_count"] == 7
    assert schedules[0]["status"] == "unchanged"


def test_set_schedule_settings_can_update_timezone_without_time(monkeypatch: MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1", "name": "Pets"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": True,
                }
            ],
        },
    )
    monkeypatch.setattr(
        core,
        "list_schedules",
        lambda repo_id: {
            "jobs": [
                {
                    "kind": "vuln-audit",
                    "enabled": True,
                    "frequency": "weekly",
                    "daysOfWeek": ["mon"],
                    "scheduleTimeSource": "user",
                    "scheduleTime": "09:00",
                    "timezone": "UTC",
                }
            ]
        },
    )

    def fake_set_schedule(_repo_id: str, _audit: AuditAlias, payload: dict[str, object]) -> dict[str, object]:
        captured.append(payload)
        return {"job": payload}

    monkeypatch.setattr(core, "set_schedule", fake_set_schedule)

    payload = core.set_schedule_settings(
        "j2h4u/enji-guard-cli",
        None,
        ScheduleSettingsUpdate(
            enabled=None,
            frequency=None,
            days_of_week=None,
            schedule_time=None,
            timezone="Asia/Almaty",
        ),
    )

    schedules = cast(list[dict[str, object]], payload["schedules"])
    assert captured[0]["timezone"] == "Asia/Almaty"
    assert captured[0]["scheduleTime"] == "09:00"
    assert schedules[0]["timezone"] == "Asia/Almaty"


def test_set_schedule_settings_can_reset_schedule_time_to_auto(monkeypatch: MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(core, "run_projects", lambda: {"projects": [{"id": "project_1", "name": "Pets"}]})
    monkeypatch.setattr(
        core,
        "run_project_detail",
        lambda project_id: {
            "project": {"id": project_id, "name": "Pets"},
            "repos": [
                {
                    "id": "repo_1",
                    "githubOwner": "j2h4u",
                    "githubName": "enji-guard-cli",
                    "connected": True,
                    "reconDone": True,
                }
            ],
        },
    )
    monkeypatch.setattr(
        core,
        "list_schedules",
        lambda repo_id: {
            "jobs": [
                {
                    "kind": "vuln-audit",
                    "enabled": True,
                    "frequency": "workdays",
                    "daysOfWeek": ["mon", "tue", "wed", "thu", "fri"],
                    "scheduleTimeSource": "user",
                    "scheduleTime": "09:00",
                    "timezone": "Asia/Almaty",
                }
            ]
        },
    )

    def fake_set_schedule(_repo_id: str, _audit: AuditAlias, payload: dict[str, object]) -> dict[str, object]:
        captured.append(payload)
        return {"job": payload}

    monkeypatch.setattr(core, "set_schedule", fake_set_schedule)

    payload = core.set_schedule_settings(
        "j2h4u/enji-guard-cli",
        None,
        ScheduleSettingsUpdate(
            enabled=None,
            frequency=None,
            days_of_week=None,
            schedule_time="auto",
            timezone=None,
        ),
    )

    schedules = cast(list[dict[str, object]], payload["schedules"])
    assert captured[0]["scheduleTimeSource"] == "auto"
    assert "scheduleTime" not in captured[0]
    assert captured[0]["timezone"] == "Asia/Almaty"
    assert schedules[0]["schedule_time_source"] == "auto"
    assert schedules[0]["schedule_time"] is None


def test_set_schedule_settings_requires_explicit_write_scope() -> None:
    with pytest.raises(ValueError, match="schedule set: pass REPO, --all-repos with --project, or --all-projects"):
        core.set_schedule_settings(
            None,
            None,
            ScheduleSettingsUpdate(
                enabled=False,
                frequency=None,
                days_of_week=None,
                schedule_time=None,
                timezone=None,
            ),
        )


def test_set_email_preferences_requires_explicit_write_scope() -> None:
    with pytest.raises(ValueError, match="email set: pass REPO, --all-repos with --project, or --all-projects"):
        core.set_email_preferences(None, "Pets", EmailPreferenceUpdate(None, False))


def test_set_schedule_settings_requires_project_for_all_repos() -> None:
    with pytest.raises(ValueError, match="schedule set: --all-repos requires --project"):
        core.set_schedule_settings(
            None,
            None,
            ScheduleSettingsUpdate(
                enabled=False,
                frequency=None,
                days_of_week=None,
                schedule_time=None,
                timezone=None,
            ),
            all_repos=True,
        )


def test_wait_for_audit_completion_ignores_unrelated_active_runs(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        core,
        "run_repo_active_runs",
        lambda repo_id: {"activeRuns": [{"actionKey": "audit.security", "id": "task_security"}]},
    )

    payload = core.wait_for_audit_completion("repo_1", AuditAlias.RECON, 1, 1)

    assert payload == {
        "repo_id": "repo_1",
        "audit": "recon",
        "idle": True,
        "elapsed_seconds": 0,
        "active_runs": [],
    }


def test_wait_for_audit_completion_ignores_completed_matching_runs(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        core,
        "run_repo_active_runs",
        lambda repo_id: {
            "activeRuns": [
                {
                    "actionKey": "audit.recon",
                    "completedAt": "2026-06-29T12:00:00Z",
                    "status": "completed",
                }
            ]
        },
    )

    payload = core.wait_for_audit_completion("repo_1", AuditAlias.RECON, 1, 1)

    assert payload["idle"] is True
    assert payload["active_runs"] == []


def test_wait_for_report_completion_succeeds_when_reports_are_ready_but_stale(monkeypatch: MonkeyPatch) -> None:
    status = _report_wait_status(
        complete=True,
        state="ready",
        out_of_date=True,
        run_status="completed",
    )
    monkeypatch.setattr(core, "report_status", lambda _repo_id: status)

    payload = core.wait_for_report_completion(
        "repo_1",
        options=ReportWaitOptions(poll_seconds=30, timeout_seconds=30, heartbeat_seconds=120),
        heartbeat=None,
    )

    assert payload["complete"] is True
    assert payload["timed_out"] is False
    assert payload["reason"] == "complete"
    assert payload["counts"]["stale"] == 1
    assert payload["stale"] == ["security"]


def test_wait_for_report_completion_times_out_when_reports_remain_missing(monkeypatch: MonkeyPatch) -> None:
    class FakeClock:
        value = 0.0

        def monotonic(self) -> float:
            self.value += 31.0
            return self.value

        def sleep(self, seconds: float) -> None:
            assert seconds >= 0.0

    status = _report_wait_status(
        complete=False,
        state="missing",
        out_of_date=None,
        run_status=None,
    )
    clock = FakeClock()
    monkeypatch.setattr(core, "report_status", lambda _repo_id: status)
    monkeypatch.setattr(core.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(core.time, "sleep", clock.sleep)

    payload = core.wait_for_report_completion(
        "repo_1",
        options=ReportWaitOptions(poll_seconds=30, timeout_seconds=30, heartbeat_seconds=120),
        heartbeat=None,
    )

    assert payload["complete"] is False
    assert payload["timed_out"] is True
    assert payload["reason"] == "timeout"
    assert payload["counts"]["missing"] == 1


def _report_wait_status(
    *,
    complete: bool,
    state: core.ReportAuditState,
    out_of_date: bool | None,
    run_status: str | None,
) -> core.ReportStatusPayload:
    ready = ["security"] if state == "ready" else []
    running = ["security"] if state == "running" else []
    missing = ["security"] if state == "missing" else []
    return {
        "repo_id": "repo_1",
        "current_head_sha": "head_2",
        "last_report_at": "2026-06-30T12:00:00Z",
        "complete": complete,
        "ready": ready,
        "running": running,
        "missing": missing,
        "reports": [
            {
                "audit": "security",
                "label": "Security",
                "action_key": "audit.security",
                "route_slug": "security",
                "state": state,
                "ready": state == "ready",
                "running": state == "running",
                "fleet_task_id": "task_1",
                "created_at": "2026-06-30T11:00:00Z",
                "started_at": "2026-06-30T11:00:00Z",
                "completed_at": "2026-06-30T12:00:00Z",
                "run_status": run_status,
                "current_head_sha": "head_2",
                "last_audited_head_sha": "head_1",
                "out_of_date": out_of_date,
            }
        ],
    }


def test_start_audit_builds_spa_compatible_fleet_task_body(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    project = {
        "repos": [
            {
                "id": "repo_1",
                "githubOwner": "j2h4u",
                "githubName": "enji-guard-cli",
                "connected": True,
            }
        ],
        "webResources": [{"url": "https://example.test", "repoIds": ["repo_1"]}],
    }
    catalog = {
        "curatedActions": [
            {
                "actionKey": "audit.recon",
                "title": "Run recon",
                "runbookKind": "recon",
                "fleetRunbookId": "runbook_1",
                "artifactSchemaName": "upfront.recon.summary",
                "artifactSchemaVersion": "v1",
                "taskDescriptionTemplate": "Repo {{repoFullName}}\n{{linkedSites}}\n{{reportSchemaName}}",
            }
        ]
    }

    def fake_start(request: object) -> dict[str, object]:
        captured["request"] = request
        return {"task": {"id": "task_1"}}

    monkeypatch.setattr(core, "run_project_detail", lambda project_id: project)
    monkeypatch.setattr(core, "run_catalog", lambda: catalog)
    monkeypatch.setattr(
        core, "run_runbook", lambda runbook_id: {"suggested_flow": "single", "suggested_flow_config": {}}
    )
    monkeypatch.setattr(core, "run_repo_active_runs", lambda repo_id: {"activeRuns": []})
    monkeypatch.setattr(core, "run_start_audit_run", fake_start)

    payload = core.start_audit("repo_1", "project_1", AuditAlias.RECON)

    assert payload == {"task": {"id": "task_1"}}
    request = captured["request"]
    assert isinstance(request, core.AuditRunCreate)
    assert request.repo_id == "repo_1"
    assert request.project_id == "project_1"
    assert request.action_key == "audit.recon"
    assert request.fleet_task_body == {
        "title": "Run recon for j2h4u/enji-guard-cli",
        "description": "Repo j2h4u/enji-guard-cli\n- https://example.test\nupfront.recon.report",
        "project_id": "project_1",
        "execution_flow": "single",
        "flow_config": {},
        "runbook_id": "runbook_1",
        "scope_type": "project",
        "scope_owner": "project_1",
        "origin_type": "manual",
        "repo_access_contexts": [{"provider": "github", "repo_full_name": "j2h4u/enji-guard-cli"}],
    }


def test_start_audit_skips_already_running_audit(monkeypatch: MonkeyPatch) -> None:
    def fail_start(request: core.AuditRunCreate) -> object:
        raise AssertionError("duplicate audit should not be started")

    monkeypatch.setattr(
        core,
        "run_repo_active_runs",
        lambda repo_id: {
            "activeRuns": [
                {
                    "actionKey": "audit.recon",
                    "fleetTaskId": "task_recon",
                    "status": "pending",
                    "completedAt": None,
                }
            ]
        },
    )
    monkeypatch.setattr(core, "run_start_audit_run", fail_start)

    payload = core.start_audit("repo_1", "project_1", AuditAlias.RECON)

    assert payload == {
        "skipped": True,
        "audit": "recon",
        "action_key": "audit.recon",
        "reason": "already_running",
        "active_runs": [
            {
                "actionKey": "audit.recon",
                "fleetTaskId": "task_recon",
                "status": "pending",
                "completedAt": None,
            }
        ],
    }
