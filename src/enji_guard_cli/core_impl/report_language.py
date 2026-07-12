from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from enji_guard_cli.enji_api import LanguageCode
from enji_guard_cli.json_types import JsonObjectPayload

type GetUserPreferences = Callable[[], JsonObjectPayload]
type PutUserLanguage = Callable[[LanguageCode], JsonObjectPayload]


@dataclass(frozen=True, slots=True)
class ReportLanguageDependencies:
    get_user_preferences: GetUserPreferences
    put_user_language: PutUserLanguage


def show_report_language(*, dependencies: ReportLanguageDependencies) -> JsonObjectPayload:
    preferred = _language_from_preferences(dependencies.get_user_preferences())
    return {
        "language": preferred,
        "scope": "account",
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
    }


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
