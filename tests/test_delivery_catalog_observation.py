# pyright: basic

import importlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from enji_guard_cli.application import Application
from enji_guard_cli.audit.ports import AuditCatalogChange, AuditCatalogResult

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

    application = Application(
        audit_gateway=cast(Any, CatalogGateway()),
        portfolio_gateway=cast(Any, None),
        auth=cast(Any, None),
    )
    constructions = 0

    def application_factory(_auth_file: object = None) -> Application:
        nonlocal constructions
        constructions += 1
        return application

    monkeypatch.setattr(cli_module, "create_application", application_factory)
    monkeypatch.setitem(cli_module._state, "application", None)
    monkeypatch.setitem(cli_module._state, "application_auth_file", None)

    cli_module._run(lambda: cli_module._application().catalog(), True)

    assert constructions == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["audit_catalog"]["changes"][0]["action_key"] == "audit.security"


def test_application_keeps_catalog_observation_isolated_per_execution() -> None:
    barrier = threading.Barrier(2)

    class CatalogGateway:
        def catalog(self) -> AuditCatalogResult:
            action_key = f"audit.{threading.current_thread().name}"
            change = AuditCatalogChange(kind="changed", action_key=action_key, changed_fields=("title",))
            return AuditCatalogResult(actions=(), changes=(change,))

    application = Application(
        audit_gateway=cast(Any, CatalogGateway()),
        portfolio_gateway=cast(Any, None),
        auth=cast(Any, None),
    )

    def execute() -> tuple[str, str]:
        def read_catalog() -> str:
            action_key = application.catalog().changes[0].action_key
            barrier.wait()
            return action_key

        result = application.execute(read_catalog)
        return cast(str, result.payload), result.catalog_changes[0].action_key

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="catalog") as pool:
        results = tuple(pool.map(lambda _index: execute(), range(2)))

    assert all(expected == observed for expected, observed in results)


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
    monkeypatch.setattr(
        cli_module,
        "_run",
        lambda _action, _as_json, _renderer=None: seen.append(str(cli_module._state["operation"])),
    )

    result = CliRunner().invoke(cli_module.app, list(args))

    assert result.exit_code == 0
    assert seen == [operation]
