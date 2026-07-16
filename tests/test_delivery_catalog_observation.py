# pyright: basic

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")


AUDIT_AWARE_OPERATIONS = (
    "cli audit start",
    "cli audit read",
    "cli audit summary",
    "cli audit status",
    "cli audit wait",
    "cli repo add",
    "cli repo list",
    "cli repo status",
    "cli recon start",
    "cli recon status",
    "cli portfolio status",
    "cli status",
    "cli wait",
    "cli schedule list",
    "cli schedule set",
    "cli schedule auto-time",
    "cli schedule timezone",
    "cli improvement-jobs list",
    "cli improvement-jobs set",
    "cli email list",
    "cli email set",
)

NON_AUDIT_AWARE_OPERATIONS = (
    "cli repo remove",
    "cli repo move",
    "cli repo resolve",
    "cli project list",
    "cli project create",
    "cli project rename",
    "cli project delete",
    "cli project settings",
    "cli access",
    "cli language show",
    "cli language set",
    "cli auth status",
)


@pytest.mark.parametrize("operation", AUDIT_AWARE_OPERATIONS)
def test_catalog_consuming_operations_are_audit_aware(operation: str) -> None:
    assert cli_module._is_audit_aware_operation(operation) is True


@pytest.mark.parametrize("operation", NON_AUDIT_AWARE_OPERATIONS)
def test_non_catalog_operations_are_not_audit_aware(operation: str) -> None:
    assert cli_module._is_audit_aware_operation(operation) is False


def test_audit_aware_run_brackets_one_catalog_fetch_without_post_fetch_refetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[tuple[str, object]] = []
    fetches = 0

    class FakeApplication:
        def catalog_observation(self) -> object:
            events.append(("read", None))
            return SimpleNamespace(changes=())

        def fetch_catalog_once(self) -> dict[str, object]:
            nonlocal fetches
            fetches += 1
            return {"ok": True}

    application = FakeApplication()
    monkeypatch.setitem(cli_module._state, "application", application)
    monkeypatch.setitem(cli_module._state, "operation", "cli repo status")
    monkeypatch.setattr(
        cli_module,
        "default_settings",
        lambda: SimpleNamespace(audit_catalog=SimpleNamespace(state_file=tmp_path / "catalog.json")),
    )
    monkeypatch.setattr(
        cli_module,
        "begin_audit_catalog_observation",
        lambda **_kwargs: events.append(("begin", None)) or "token",
    )
    monkeypatch.setattr(cli_module, "end_audit_catalog_observation", lambda token: events.append(("end", token)))

    cli_module._run(application.fetch_catalog_once, True)

    assert fetches == 1
    assert [kind for kind, _value in events] == ["begin", "read", "end"]


@pytest.mark.parametrize("operation", ("cli repo remove", "cli repo move", "cli repo resolve"))
def test_repository_mutations_and_resolution_skip_catalog_observation(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    events: list[str] = []

    class FakeApplication:
        def catalog_observation(self) -> object:
            events.append("read")
            raise AssertionError("non-catalog operation must not read observation")

    monkeypatch.setitem(cli_module._state, "application", FakeApplication())
    monkeypatch.setitem(cli_module._state, "operation", operation)
    monkeypatch.setattr(cli_module, "begin_audit_catalog_observation", lambda **_kwargs: events.append("begin"))
    monkeypatch.setattr(cli_module, "end_audit_catalog_observation", lambda _token: events.append("end"))

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
