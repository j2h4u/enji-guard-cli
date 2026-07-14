from typing import cast

from enji_guard_cli.audit import AuditDefinition
from enji_guard_cli.core_impl.models import EmailPreferenceUpdate, RepoTargetPayload
from enji_guard_cli.core_impl.payloads import json_bool, json_dict
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue


def email_preferences_patch(update: EmailPreferenceUpdate) -> JsonObjectPayload:
    patch: JsonObjectPayload = {}
    if update.manual_run_completion is not None:
        patch["manualRunCompletion"] = update.manual_run_completion
    if update.scheduled_run_completion is not None:
        patch["scheduledRunCompletion"] = update.scheduled_run_completion
    if not patch:
        raise ValueError("pass --manual or --scheduled")
    return patch


def email_preference_row(
    target: RepoTargetPayload,
    audit: AuditDefinition,
    payload: JsonObjectPayload,
) -> dict[str, JsonValue]:
    resolved = json_dict(payload.get("resolved"))
    return {
        "project_id": target["project_id"],
        "project_name": target["project_name"],
        "repo_id": target["repo_id"],
        "github_repo": target["github_repo"],
        "audit": audit.action_key,
        "action_key": audit.action_key,
        "manual_run_completion": json_bool(resolved.get("manualRunCompletion")),
        "scheduled_run_completion": json_bool(resolved.get("scheduledRunCompletion")),
    }


def email_preferences_payload(rows: list[dict[str, JsonValue]]) -> JsonObjectPayload:
    preferences = [cast(JsonValue, row) for row in rows]
    return {
        "preferences": preferences,
        "summary": {
            "repo_count": repo_count(rows),
            "audit_count": len(rows),
        },
    }


def repo_count(rows: list[dict[str, JsonValue]]) -> int:
    return len({repo_id for row in rows if isinstance(repo_id := row.get("repo_id"), str)})
