"""The composed MCP query surface must always be closeable.

The facade is deliberately narrow, so nothing but composition can reach the
application (and its pooled HTTP client) underneath it.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import cast

import pytest
from mcp.server.fastmcp import FastMCP

import enji_guard_cli.delivery.mcp.server as server_module
from enji_guard_cli.composition import mcp_query_facade
from enji_guard_cli.delivery.mcp.server import create_mcp_server
from enji_guard_cli.mcp_facade import McpQueryFacade
from enji_guard_cli.transport import EnjiHttpRequest, HttpxEnjiHttpClient


def _owned_http_client(queries: McpQueryFacade) -> HttpxEnjiHttpClient:
    """The pooled client the composed application owns and must release."""
    client = queries._runner.lifecycle
    assert isinstance(client, HttpxEnjiHttpClient)
    return client


@asynccontextmanager
async def _running_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Drive the server lifespan exactly as a transport run loop would."""
    mcp_server = server._mcp_server
    async with mcp_server.lifespan(mcp_server):
        yield


def test_scoped_query_facade_closes_its_application_and_pool(tmp_path: Path) -> None:
    with mcp_query_facade(tmp_path / "auth.json") as queries:
        runner = queries._runner
        client = _owned_http_client(queries)
        assert client.is_closed is False

    assert client.is_closed is True
    # The pool is genuinely released, not merely flagged: httpx refuses to send
    # on a closed client, so this cannot pass if close() were skipped.
    with pytest.raises(RuntimeError, match="client has been closed"):
        client.request_blocking(
            EnjiHttpRequest(
                method="GET",
                url="http://127.0.0.1:1/unreachable",
                operation="probe",
                headers={},
            )
        )
    with pytest.raises(RuntimeError, match="application is closed"):
        runner.execute(lambda: None)


def test_creating_the_server_composes_nothing_before_the_lifespan_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        del auth_file
        raise AssertionError("creating the server must not compose a query surface")

    monkeypatch.setattr(server_module, "mcp_query_facade", refuse)

    server = create_mcp_server()

    assert server.name == "enji-guard-cli"


def test_server_lifespan_releases_the_pool_it_composed(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the real lifespan must leave no open connection pool behind."""
    captured: list[HttpxEnjiHttpClient] = []

    @contextmanager
    def recording_facade(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        """Record the real composed client without changing ownership."""
        with mcp_query_facade(auth_file) as facade:
            captured.append(_owned_http_client(facade))
            yield facade

    monkeypatch.setattr(server_module, "mcp_query_facade", recording_facade)
    server = create_mcp_server()

    async def exercise() -> None:
        async with _running_lifespan(server):
            assert len(captured) == 1
            assert captured[0].is_closed is False

    asyncio.run(exercise())

    assert captured[0].is_closed is True


def test_server_lifespan_owns_and_closes_the_composed_query_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    facade = cast(McpQueryFacade, object())
    opened: list[McpQueryFacade] = []
    closed: list[McpQueryFacade] = []

    @contextmanager
    def scoped_facade(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        del auth_file
        opened.append(facade)
        try:
            yield facade
        finally:
            closed.append(facade)

    monkeypatch.setattr(server_module, "mcp_query_facade", scoped_facade)
    server = create_mcp_server()

    async def exercise() -> None:
        async with _running_lifespan(server):
            assert opened == [facade]
            assert closed == []

    asyncio.run(exercise())

    assert closed == [facade]


def test_injected_query_surface_stays_owned_by_its_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        del auth_file
        raise AssertionError("an injected query surface must not be composed again")

    monkeypatch.setattr(server_module, "mcp_query_facade", refuse)
    server = create_mcp_server(queries=cast(McpQueryFacade, object()))

    async def exercise() -> None:
        async with _running_lifespan(server):
            pass

    asyncio.run(exercise())
