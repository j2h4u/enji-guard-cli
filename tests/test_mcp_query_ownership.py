"""The composed MCP query surface must always be closeable.

The facade is deliberately narrow, so nothing but composition can reach the
application (and its pooled HTTP client plus owner thread) underneath it.
"""

import asyncio
import threading
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


def _http_pool_threads() -> int:
    return sum(1 for thread in threading.enumerate() if thread.name == "enji-guard-http")


@asynccontextmanager
async def _running_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Drive the server lifespan exactly as a transport run loop would."""
    mcp_server = server._mcp_server
    async with mcp_server.lifespan(mcp_server):
        yield


def test_scoped_query_facade_closes_its_application_and_pool(tmp_path: Path) -> None:
    baseline = _http_pool_threads()

    with mcp_query_facade(tmp_path / "auth.json") as queries:
        assert _http_pool_threads() == baseline + 1
        application = queries._application

    assert _http_pool_threads() == baseline
    with pytest.raises(RuntimeError, match="application is closed"):
        application.execute(lambda: None)


def test_creating_the_server_composes_nothing_before_the_lifespan_runs() -> None:
    baseline = _http_pool_threads()

    server = create_mcp_server()

    assert _http_pool_threads() == baseline
    assert server.name == "enji-guard-cli"


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
