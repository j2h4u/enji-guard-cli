from enji_guard_cli.core_impl.report_language import (
    ReportLanguageDependencies,
    set_report_language,
    show_report_language,
)
from enji_guard_cli.json_types import JsonObjectPayload


def dependencies(*, language: str = "ru") -> ReportLanguageDependencies:
    return ReportLanguageDependencies(
        list_projects=lambda: {
            "projects": [
                {"id": "project_1", "name": "Pets"},
                {"id": "project_2", "name": "MCP Integrations"},
            ]
        },
        get_user_preferences=lambda: {"preferences": {"language": language}},
        put_user_language=lambda selected: {"preferences": {"language": selected}},
        get_project_run_language=lambda _project_id: {"language": language},
    )


def test_show_report_language_returns_account_preference_and_effective_projects() -> None:
    assert show_report_language(dependencies=dependencies()) == {
        "language": "ru",
        "scope": "account",
        "projects": [
            {"project_id": "project_1", "project_name": "Pets", "language": "ru"},
            {"project_id": "project_2", "project_name": "MCP Integrations", "language": "ru"},
        ],
    }


def test_set_report_language_is_idempotent() -> None:
    writes: list[str] = []
    deps = dependencies()
    deps = ReportLanguageDependencies(
        deps.list_projects,
        deps.get_user_preferences,
        lambda language: writes.append(language) or {"preferences": {"language": language}},
        deps.get_project_run_language,
    )

    payload = set_report_language(" RU ", dependencies=deps)

    assert payload["changed"] is False
    assert writes == []


def test_set_report_language_writes_changed_value_and_rechecks_projects() -> None:
    writes: list[str] = []
    effective: JsonObjectPayload = {"language": "en"}
    deps = ReportLanguageDependencies(
        list_projects=lambda: {"projects": [{"id": "project_1", "name": "Pets"}]},
        get_user_preferences=lambda: {"preferences": {"language": "ru"}},
        put_user_language=lambda language: writes.append(language) or effective.update(language=language) or {},
        get_project_run_language=lambda _project_id: effective,
    )

    payload = set_report_language("en", dependencies=deps)

    assert payload == {
        "language": "en",
        "previous_language": "ru",
        "scope": "account",
        "changed": True,
        "projects": [{"project_id": "project_1", "project_name": "Pets", "language": "en"}],
    }
    assert writes == ["en"]


def test_set_report_language_rejects_unknown_language() -> None:
    try:
        set_report_language("de", dependencies=dependencies())
    except ValueError as exc:
        assert str(exc) == "language must be en or ru"
    else:
        raise AssertionError("unknown language was accepted")
