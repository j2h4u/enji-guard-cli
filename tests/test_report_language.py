from enji_guard_cli.core_impl.report_language import (
    ReportLanguageDependencies,
    set_report_language,
    show_report_language,
)


def dependencies(*, language: str = "ru") -> ReportLanguageDependencies:
    return ReportLanguageDependencies(
        get_user_preferences=lambda: {"preferences": {"language": language}},
        put_user_language=lambda selected: {"preferences": {"language": selected}},
    )


def test_show_report_language_returns_account_preference() -> None:
    assert show_report_language(dependencies=dependencies()) == {
        "language": "ru",
        "scope": "account",
    }


def test_set_report_language_is_idempotent() -> None:
    writes: list[str] = []
    deps = dependencies()
    deps = ReportLanguageDependencies(
        deps.get_user_preferences,
        lambda language: writes.append(language) or {"preferences": {"language": language}},
    )

    payload = set_report_language(" RU ", dependencies=deps)

    assert payload["changed"] is False
    assert writes == []


def test_set_report_language_writes_changed_value() -> None:
    writes: list[str] = []
    deps = ReportLanguageDependencies(
        get_user_preferences=lambda: {"preferences": {"language": "ru"}},
        put_user_language=lambda language: writes.append(language) or {},
    )

    payload = set_report_language("en", dependencies=deps)

    assert payload == {
        "language": "en",
        "previous_language": "ru",
        "scope": "account",
        "changed": True,
    }
    assert writes == ["en"]


def test_set_report_language_rejects_unknown_language() -> None:
    try:
        set_report_language("de", dependencies=dependencies())
    except ValueError as exc:
        assert str(exc) == "language must be en or ru"
    else:
        raise AssertionError("unknown language was accepted")
