"""Public client lifecycle and optional-service import contracts."""

import subprocess
import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

import enji_guard_cli.client as client_module
import enji_guard_cli.delivery.service as service_module
from enji_guard_cli.client import ClientResult, EnjiGuardClient, EnjiGuardError
from enji_guard_cli.client_facade import ClientQueryCatalogChange, ClientQueryError, ClientQueryResult


class _Facade:
    def __init__(self, result: ClientQueryResult | Exception) -> None:
        self.result = result
        self.selectors: list[str] | None = None

    def portfolio_overview(self, _project: str | None, _sort: str) -> ClientQueryResult:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def repository_status(self, _repository: str, _project: str | None) -> ClientQueryResult:
        return self.portfolio_overview(None, "default")

    def audit_summary(self, _repository: str, _project: str | None) -> ClientQueryResult:
        return self.portfolio_overview(None, "default")

    def audit_read(self, _repository: str, audits: list[str], _project: str | None) -> ClientQueryResult:
        self.selectors = audits
        return self.portfolio_overview(None, "default")


def _scope(facade: _Facade, events: list[str]) -> AbstractContextManager[_Facade]:
    @contextmanager
    def managed() -> Iterator[_Facade]:
        events.append("open")
        try:
            yield facade
        finally:
            events.append("close")

    return managed()


def test_client_requires_context_and_returns_provider_neutral_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    facade = _Facade(
        ClientQueryResult(
            {"repository": "github@github.com:owner/repo"},
            True,
            (ClientQueryCatalogChange("audit.security", ("title",), "changed"),),
        )
    )
    monkeypatch.setattr(client_module, "client_query_facade", lambda _auth_file: _scope(facade, events))
    client = EnjiGuardClient(Path("auth.json"))

    with pytest.raises(RuntimeError, match="context manager"):
        client.portfolio_overview()

    with client:
        result = client.portfolio_overview()

    assert result == ClientResult(
        data={"repository": "github@github.com:owner/repo"},
        catalog_observed=True,
        catalog_changes=(client_module.ClientCatalogChange("audit.security", ("title",), "changed"),),
    )
    assert events == ["open", "close"]
    with pytest.raises(RuntimeError, match="context manager"):
        client.portfolio_overview()


def test_client_closes_its_scope_on_query_exception_and_translates_application_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    facade = _Facade(ClientQueryError("AUTH_REQUIRED", "credential missing"))
    monkeypatch.setattr(client_module, "client_query_facade", lambda _auth_file: _scope(facade, events))

    with EnjiGuardClient() as client, pytest.raises(EnjiGuardError, match="credential missing") as raised:
        client.portfolio_overview()

    assert raised.value.code == "AUTH_REQUIRED"
    assert events == ["open", "close"]


def test_client_normalizes_explicit_audit_selectors_and_rejects_empty_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    facade = _Facade(ClientQueryResult({"audits": []}, False, ()))
    monkeypatch.setattr(client_module, "client_query_facade", lambda _auth_file: _scope(facade, events))

    with EnjiGuardClient() as client:
        result = client.audit_read("github@github.com:owner/repo", [" audit.security ", "quality"])
        assert result.catalog_changes == ()
        with pytest.raises(EnjiGuardError, match="non-empty audit selector") as raised:
            client.audit_read("github@github.com:owner/repo", [" ", "audit."])
        with pytest.raises(EnjiGuardError, match="sequence, not a string") as string_raised:
            client.audit_read("github@github.com:owner/repo", "audit.security")  # type: ignore[arg-type]

    assert facade.selectors == ["security", "quality"]
    assert raised.value.code == "VALIDATION"
    assert string_raised.value.code == "VALIDATION"


def test_service_reports_missing_mcp_extra_without_masking_other_import_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_extra() -> tuple[object, object]:
        raise service_module.EnjiGuardMcpExtraRequiredError

    monkeypatch.setattr(service_module, "_mcp_implementation", missing_extra)
    result = CliRunner().invoke(service_module.app, [])

    assert result.exit_code == 2
    assert result.stderr == "MCP_EXTRA_REQUIRED: install 'enji-guard-cli[mcp]' to run the MCP service\n"

    def broken_implementation() -> tuple[object, object]:
        raise ModuleNotFoundError("broken dependency", name="unrelated_dependency")

    monkeypatch.setattr(service_module, "_mcp_implementation", broken_implementation)
    with pytest.raises(ModuleNotFoundError, match="broken dependency"):
        service_module.run(service_module.RuntimeServiceOptions(transport="stdio", host="127.0.0.1", port=18081))


def test_cli_import_does_not_load_the_optional_mcp_dependency() -> None:
    script = """
import builtins
import sys
original_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'mcp' or name.startswith('mcp.'):
        raise ModuleNotFoundError('blocked optional dependency', name='mcp')
    return original_import(name, *args, **kwargs)
builtins.__import__ = blocked
import enji_guard_cli.delivery.cli.app
assert not any(name == 'mcp' or name.startswith('mcp.') for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
