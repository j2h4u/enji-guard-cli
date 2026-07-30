# pyright: basic

import importlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import cast

import pytest
from typer.testing import CliRunner

from application_builder import ApplicationStubs
from enji_guard_cli.application import Application
from enji_guard_cli.audit.ports import AuditCatalogChange, AuditCatalogResult
from enji_guard_cli.delivery.cli.presentation import FIELDS_PRESENTATION

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")


def test_run_emits_catalog_changes_from_the_command_application(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    change = AuditCatalogChange(
        kind="changed",
        action_key="audit.security",
        changed_fields=("title",),
    )

    class CatalogGateway:
        def catalog(self) -> AuditCatalogResult:
            return AuditCatalogResult(actions=(), changes=(change,))

    application = ApplicationStubs(audit_gateway=CatalogGateway()).build()
    constructions = 0

    def application_factory(_auth_file: object = None) -> Application:
        nonlocal constructions
        constructions += 1
        return application

    monkeypatch.setattr(cli_module, "create_application", application_factory)
    monkeypatch.setitem(cli_module._state, "application", None)
    monkeypatch.setitem(cli_module._state, "application_auth_file", None)

    cli_module._run(lambda: cli_module._application().catalog.catalog(), True, FIELDS_PRESENTATION)

    assert constructions == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["audit_catalog"]["changes"][0]["action_key"] == "audit.security"


def test_run_emits_an_empty_catalog_section_after_an_unchanged_catalog_observation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class CatalogGateway:
        def catalog(self) -> AuditCatalogResult:
            return AuditCatalogResult(actions=())

    application = ApplicationStubs(audit_gateway=CatalogGateway()).build()
    monkeypatch.setattr(cli_module, "create_application", lambda _auth_file=None: application)
    monkeypatch.setitem(cli_module._state, "application", None)
    monkeypatch.setitem(cli_module._state, "application_auth_file", None)

    cli_module._run(lambda: cli_module._application().catalog.catalog(), True, FIELDS_PRESENTATION)

    payload = json.loads(capsys.readouterr().out)
    assert payload["audit_catalog"] == {"changes": []}


def test_run_does_not_add_catalog_section_without_a_catalog_observation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = ApplicationStubs().build()
    monkeypatch.setattr(cli_module, "create_application", lambda _auth_file=None: application)
    monkeypatch.setitem(cli_module._state, "application", None)
    monkeypatch.setitem(cli_module._state, "application_auth_file", None)

    cli_module._run(lambda: {"value": "no catalog"}, True, FIELDS_PRESENTATION)

    assert json.loads(capsys.readouterr().out) == {"value": "no catalog"}


def test_application_keeps_catalog_observation_isolated_per_execution() -> None:
    barrier = threading.Barrier(2)

    class CatalogGateway:
        def catalog(self) -> AuditCatalogResult:
            action_key = f"audit.{threading.current_thread().name}"
            change = AuditCatalogChange(kind="changed", action_key=action_key, changed_fields=("title",))
            return AuditCatalogResult(actions=(), changes=(change,))

    application = ApplicationStubs(audit_gateway=CatalogGateway()).build()

    def execute() -> tuple[str, str]:
        def read_catalog() -> str:
            action_key = application.catalog.catalog().changes[0].action_key
            barrier.wait()
            return action_key

        result = application.runner.execute(read_catalog)
        return cast(str, result.payload), result.catalog_changes[0].action_key

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="catalog") as pool:
        results = tuple(pool.map(lambda _index: execute(), range(2)))

    assert all(expected == observed for expected, observed in results)


@pytest.mark.parametrize(
    ("args", "operation"),
    [
        (("status", "repo-1"), "cli status"),
        (("repo", "remove", "repo-1", "--yes"), "cli repo remove"),
        (("repo", "move", "repo-1", "--to-project", "project-2"), "cli repo move"),
        (("repo", "resolve", "repo-1"), "cli repo resolve"),
    ],
)
def test_cli_callbacks_set_the_operation_names_used_by_observation(
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    operation: str,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        cli_module,
        "_run",
        lambda _action, _as_json, _renderer=None: seen.append(str(cli_module._state["operation"])),
    )

    result = CliRunner().invoke(cli_module.app, list(args))

    assert result.exit_code == 0
    assert seen == [operation]
