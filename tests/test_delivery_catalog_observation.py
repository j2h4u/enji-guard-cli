# pyright: basic

import importlib

import pytest
from typer.testing import CliRunner

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")


def test_run_uses_application_result_without_reading_internal_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    fetches = 0

    class FakeApplication:
        def catalog_observation(self) -> object:
            events.append(("read", None))
            raise AssertionError("CLI must not inspect Application internals")

        def fetch_catalog_once(self) -> dict[str, object]:
            nonlocal fetches
            fetches += 1
            return {"ok": True}

    application = FakeApplication()
    monkeypatch.setitem(cli_module._state, "application", application)
    monkeypatch.setitem(cli_module._state, "operation", "cli repo status")
    cli_module._run(application.fetch_catalog_once, True)

    assert fetches == 1
    assert events == []


def test_run_does_not_read_application_observation_for_successful_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeApplication:
        def catalog_observation(self) -> object:
            events.append("read")
            raise AssertionError("CLI must not inspect Application internals")

    monkeypatch.setitem(cli_module._state, "application", FakeApplication())
    monkeypatch.setitem(cli_module._state, "operation", "cli repo remove")

    cli_module._run(lambda: {"ok": True}, True)

    assert events == []


@pytest.mark.parametrize(
    ("args", "operation"),
    [
        (("audit", "status", "repo-1"), "cli audit status"),
        (("repo", "remove", "repo-1"), "cli repo remove"),
        (("repo", "move", "repo-1", "--to-project", "project-2"), "cli repo move"),
        (("repo", "resolve", "repo-1"), "cli repo resolve"),
        (("portfolio", "status"), "cli portfolio status"),
    ],
)
def test_cli_callbacks_set_the_operation_names_used_by_observation(
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    operation: str,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(cli_module, "_run", lambda _action, _as_json: seen.append(str(cli_module._state["operation"])))

    result = CliRunner().invoke(cli_module.app, list(args))

    assert result.exit_code == 0
    assert seen == [operation]
