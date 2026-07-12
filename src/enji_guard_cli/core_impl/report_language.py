from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from enji_guard_cli.enji_api import LanguageCode
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

type ListProjects = Callable[[], JsonObjectPayload]
type GetUserPreferences = Callable[[], JsonObjectPayload]
type PutUserLanguage = Callable[[LanguageCode], JsonObjectPayload]
type GetProjectRunLanguage = Callable[[str], JsonObjectPayload]


@dataclass(frozen=True, slots=True)
class ReportLanguageDependencies:
    list_projects: ListProjects
    get_user_preferences: GetUserPreferences
    put_user_language: PutUserLanguage
    get_project_run_language: GetProjectRunLanguage


def show_report_language(*, dependencies: ReportLanguageDependencies) -> JsonObjectPayload:
    preferred = _language_from_preferences(dependencies.get_user_preferences())
    projects = _project_languages(dependencies)
    return {
        "language": preferred,
        "scope": "account",
        "projects": projects,
    }


def set_report_language(
    language: str,
    *,
    dependencies: ReportLanguageDependencies,
) -> JsonObjectPayload:
    desired = _validated_language(language)
    current = _language_from_preferences(dependencies.get_user_preferences())
    changed = current != desired
    if changed:
        dependencies.put_user_language(desired)
    return {
        "language": desired,
        "previous_language": current,
        "scope": "account",
        "changed": changed,
        "projects": _project_languages(dependencies),
    }


def _project_languages(dependencies: ReportLanguageDependencies) -> list[JsonValue]:
    projects = dependencies.list_projects().get("projects")
    if not isinstance(projects, list):
        return []
    rows: list[JsonValue] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        project_id = project.get("id")
        if not isinstance(project_id, str) or not project_id:
            continue
        effective = dependencies.get_project_run_language(project_id).get("language")
        rows.append(
            {
                "project_id": project_id,
                "project_name": project.get("name") if isinstance(project.get("name"), str) else None,
                "language": effective if isinstance(effective, str) else None,
            }
        )
    return rows


def _language_from_preferences(payload: JsonObjectPayload) -> str | None:
    preferences = payload.get("preferences")
    if not isinstance(preferences, dict):
        return None
    language = preferences.get("language")
    return language if isinstance(language, str) else None


def _validated_language(language: str) -> LanguageCode:
    normalized = language.strip().lower()
    if normalized not in {"en", "ru"}:
        raise ValueError("language must be en or ru")
    return cast(LanguageCode, normalized)
