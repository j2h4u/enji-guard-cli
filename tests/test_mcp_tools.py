"""The two curated MCP tools are the whole agent-facing product surface.

`test_mcp_query_ownership.py` drives the lifespan; nothing drove the tool
bodies.  These tests call them the way an MCP client does -- through
`FastMCP.call_tool` -- so argument normalisation, the journey's selector
classification, the off-loop dispatch, the JSON projection of the payload,
and error propagation are all exercised against the real server object.
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import cast

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.lowlevel.server import Server, request_ctx
from mcp.server.session import ServerSession
from mcp.shared.context import RequestContext

import enji_guard_cli.delivery.mcp.server as server_module
from enji_guard_cli.application import ApplicationResult
from enji_guard_cli.application.errors import ApplicationCommandError
from enji_guard_cli.delivery.mcp.server import MCP_TOOL_NAMES, create_mcp_server, run_mcp_server_async
from enji_guard_cli.mcp_facade import McpQueryFacade
from enji_guard_cli.runtime_observability.journey import AgentJourney, JourneyBody, run_agent_journey
from enji_guard_cli.settings import RepoSettings, RepositorySortName, default_settings


@dataclass(frozen=True, slots=True)
class OverviewCall:
    project: str | None
    sort: RepositorySortName
    thread_id: int


@dataclass(frozen=True, slots=True)
class AuditsCall:
    repo: str
    project: str | None
    audit_selectors: tuple[str, ...]
    thread_id: int


@dataclass
class RecordingQueryFacade:
    """Stand in for the composed query surface, recording typed arguments."""

    payload: object = None
    failure: ApplicationCommandError | None = None
    overview_calls: list[OverviewCall] = field(default_factory=list)
    audits_calls: list[AuditsCall] = field(default_factory=list)

    def portfolio_overview(self, project: str | None, sort: RepositorySortName) -> ApplicationResult:
        self.overview_calls.append(OverviewCall(project, sort, threading.get_ident()))
        return self._result()

    def repository_audits(
        self, repo: str, project: str | None, audit_selectors: list[str] | None = None
    ) -> ApplicationResult:
        self.audits_calls.append(AuditsCall(repo, project, tuple(audit_selectors or ()), threading.get_ident()))
        return self._result()

    def _result(self) -> ApplicationResult:
        if self.failure is not None:
            raise self.failure
        return ApplicationResult(payload=self.payload)


@dataclass(frozen=True, slots=True)
class RecordedJourney:
    operation: str
    surface: str
    provenance: str | None
    selector_kind: str


@dataclass
class _ServedTools:
    server: FastMCP
    facade: RecordingQueryFacade
    journeys: list[RecordedJourney]


def _served(monkeypatch: pytest.MonkeyPatch, facade: RecordingQueryFacade) -> _ServedTools:
    """Build the real server over a recording facade and a recording journey."""
    journeys: list[RecordedJourney] = []

    @contextmanager
    def scoped_facade(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        del auth_file
        yield cast(McpQueryFacade, facade)

    def recording_journey(body: object, journey: AgentJourney) -> object:
        journeys.append(RecordedJourney(journey.operation, journey.surface, journey.provenance, journey.selector_kind))
        return run_agent_journey(cast(JourneyBody, body), journey)

    monkeypatch.setattr(server_module, "mcp_query_facade", scoped_facade)
    monkeypatch.setattr(server_module, "run_agent_journey", recording_journey)
    return _ServedTools(create_mcp_server(), facade, journeys)


@asynccontextmanager
async def _running(server: FastMCP) -> AsyncIterator[McpQueryFacade]:
    mcp_server = cast(Server[McpQueryFacade, object], server._mcp_server)
    async with mcp_server.lifespan(mcp_server) as facade:
        yield facade


@asynccontextmanager
async def _request_context(facade: McpQueryFacade) -> AsyncIterator[None]:
    request = RequestContext(
        request_id="test-request",
        meta=None,
        session=cast(ServerSession, object()),
        lifespan_context=facade,
    )
    token = request_ctx.set(request)
    try:
        yield
    finally:
        request_ctx.reset(token)


def _call(served: _ServedTools, name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Invoke one tool and return the structured content an MCP client receives."""

    async def exercise() -> object:
        async with _running(served.server) as facade, _request_context(facade):
            return await served.server.call_tool(name, arguments)

    unstructured, structured = cast(tuple[object, dict[str, object]], asyncio.run(exercise()))
    del unstructured
    return structured


@dataclass(frozen=True, slots=True)
class SampleReport:
    """A payload leaf that is not JSON by itself; `_json` must flatten it."""

    label: str
    generated_on: date


def test_portfolio_overview_normalizes_the_project_and_serializes_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade = RecordingQueryFacade(
        payload={
            "projects": ["acme"],
            "report": SampleReport("weekly", date(2026, 1, 2)),
            "artifact": Path("/var/lib/enji/report.json"),
        }
    )
    served = _served(monkeypatch, facade)

    structured = _call(served, MCP_TOOL_NAMES[0], {"project": "  acme  ", "sort": "name"})

    assert facade.overview_calls[0].project == "acme"
    assert facade.overview_calls[0].sort == "name"
    assert structured == {
        "projects": ["acme"],
        "report": {"label": "weekly", "generated_on": "2026-01-02"},
        "artifact": "/var/lib/enji/report.json",
    }


def test_portfolio_overview_treats_a_blank_project_as_account_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    facade = RecordingQueryFacade(payload={"projects": []})
    served = _served(monkeypatch, facade)

    _call(served, MCP_TOOL_NAMES[0], {"project": "   "})

    assert facade.overview_calls[0].project is None
    assert facade.overview_calls[0].sort == "default"
    assert served.journeys == [RecordedJourney(MCP_TOOL_NAMES[0], "mcp", "mcp", "unknown")]


def test_portfolio_overview_uses_configured_default_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(default_settings(), repo=RepoSettings(default_sort="weakest"))
    monkeypatch.setattr(server_module, "default_settings", lambda: settings)
    facade = RecordingQueryFacade(payload={"projects": []})
    served = _served(monkeypatch, facade)

    _call(served, MCP_TOOL_NAMES[0], {"project": ""})

    assert facade.overview_calls[0].sort == "weakest"


def test_portfolio_overview_reports_a_project_selector_when_one_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    served = _served(monkeypatch, RecordingQueryFacade(payload={"projects": []}))

    _call(served, MCP_TOOL_NAMES[0], {"project": "acme"})

    assert served.journeys == [RecordedJourney(MCP_TOOL_NAMES[0], "mcp", "mcp", "project")]


@pytest.mark.parametrize(
    ("repo", "selector_kind"),
    [("acme/web", "repository_locator"), ("repo_42", "repository_id"), ("web", "repository_id")],
)
def test_repository_audits_classifies_the_repository_selector(
    monkeypatch: pytest.MonkeyPatch, repo: str, selector_kind: str
) -> None:
    served = _served(monkeypatch, RecordingQueryFacade(payload={"audits": []}))

    _call(served, MCP_TOOL_NAMES[1], {"repo": repo})

    assert served.journeys == [RecordedJourney(MCP_TOOL_NAMES[1], "mcp", "mcp", selector_kind)]


def test_repository_audits_strips_both_selectors_and_serializes_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade = RecordingQueryFacade(payload={"audits": [{"generated_on": date(2026, 3, 4)}]})
    served = _served(monkeypatch, facade)

    structured = _call(served, MCP_TOOL_NAMES[1], {"repo": " acme/web ", "project": " acme "})

    assert facade.audits_calls[0].repo == "acme/web"
    assert facade.audits_calls[0].project == "acme"
    assert structured == {"audits": [{"generated_on": "2026-03-04"}]}


def test_repository_audits_treats_a_blank_project_as_account_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    facade = RecordingQueryFacade(payload={"audits": []})
    served = _served(monkeypatch, facade)

    _call(served, MCP_TOOL_NAMES[1], {"repo": "repo_42", "project": ""})

    assert facade.audits_calls[0].project is None


def test_repository_audits_forwards_explicit_selectors_for_full_report_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade = RecordingQueryFacade(payload={"audits": []})
    served = _served(monkeypatch, facade)

    _call(served, MCP_TOOL_NAMES[1], {"repo": "repo_42", "audits": ["security"]})

    assert facade.audits_calls[0].repo == "repo_42"
    assert facade.audits_calls[0].audit_selectors == ("security",)


def test_repository_audits_are_compact_until_an_audit_selector_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CompactFirstFacade(RecordingQueryFacade):
        def repository_audits(
            self, repo: str, project: str | None, audit_selectors: list[str] | None = None
        ) -> ApplicationResult:
            super().repository_audits(repo, project, audit_selectors)
            if audit_selectors:
                return ApplicationResult(payload={"audits": [{"artifact": {"body": "# Security"}}]})
            return ApplicationResult(payload={"audits": [{"score": 73, "freshness": "fresh"}]})

    served = _served(monkeypatch, CompactFirstFacade())

    compact = _call(served, MCP_TOOL_NAMES[1], {"repo": "repo_42"})
    selected = _call(served, MCP_TOOL_NAMES[1], {"repo": "repo_42", "audits": ["security"]})

    assert compact == {"audits": [{"score": 73, "freshness": "fresh"}]}
    assert selected == {"audits": [{"artifact": {"body": "# Security"}}]}


def test_tool_bodies_run_off_the_event_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """The query surface is blocking; running it inline would stall the server."""
    facade = RecordingQueryFacade(payload={"projects": []})
    served = _served(monkeypatch, facade)
    loop_threads: list[int] = []

    async def exercise() -> None:
        async with _running(served.server) as facade, _request_context(facade):
            loop_threads.append(threading.get_ident())
            await served.server.call_tool(MCP_TOOL_NAMES[0], {"project": "acme"})

    asyncio.run(exercise())

    assert facade.overview_calls[0].thread_id != loop_threads[0]


def test_an_application_failure_reaches_the_mcp_client_as_a_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A swallowed failure would hand the agent an empty, plausible result."""
    served = _served(
        monkeypatch,
        RecordingQueryFacade(failure=ApplicationCommandError("NOT_FOUND", "no such repository", 4)),
    )

    with pytest.raises(ToolError, match="no such repository"):
        _call(served, MCP_TOOL_NAMES[1], {"repo": "acme/web"})


def test_the_tools_refuse_to_run_outside_the_server_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    served = _served(monkeypatch, RecordingQueryFacade(payload={"projects": []}))

    async def exercise() -> None:
        await served.server.call_tool(MCP_TOOL_NAMES[0], {"project": "acme"})

    with pytest.raises(ToolError, match="only while the server lifespan is running"):
        asyncio.run(exercise())


def test_concurrent_lifespans_keep_each_request_on_its_own_query_facade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second MCP session must not replace the first session's query surface."""

    class ScopedFacade(RecordingQueryFacade):
        def portfolio_overview(self, project: str | None, sort: RepositorySortName) -> ApplicationResult:
            barrier.wait()
            return super().portfolio_overview(project, sort)

    barrier = threading.Barrier(2)
    facades = iter((ScopedFacade(payload={"scope": "one"}), ScopedFacade(payload={"scope": "two"})))

    @contextmanager
    def scoped_facade(auth_file: Path | None = None) -> Iterator[McpQueryFacade]:
        del auth_file
        yield cast(McpQueryFacade, next(facades))

    monkeypatch.setattr(server_module, "mcp_query_facade", scoped_facade)
    server = create_mcp_server()

    async def exercise() -> list[dict[str, object]]:
        facades: asyncio.Queue[McpQueryFacade] = asyncio.Queue()
        release_lifespans = asyncio.Event()

        async def lifespan_task() -> None:
            async with _running(server) as facade:
                await facades.put(facade)
                await release_lifespans.wait()

        lifespan_tasks = [asyncio.create_task(lifespan_task()) for _ in range(2)]
        try:
            first, second = await facades.get(), await facades.get()

            async def request(facade: McpQueryFacade) -> dict[str, object]:
                async with _request_context(facade):
                    _, structured = cast(
                        tuple[object, dict[str, object]],
                        await server.call_tool(MCP_TOOL_NAMES[0], {"project": "acme"}),
                    )
                    return structured

            return list(await asyncio.gather(request(first), request(second)))
        finally:
            release_lifespans.set()
            await asyncio.gather(*lifespan_tasks)

    results = asyncio.run(exercise())

    assert {cast(str, result["scope"]) for result in results} == {"one", "two"}


@dataclass
class RecordingTransportServer:
    """Record which transport coroutine `run_mcp_server_async` awaited."""

    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def run_stdio_async(self) -> None:
        self.calls.append(("stdio", None))

    async def run_sse_async(self, mount_path: str | None) -> None:
        self.calls.append(("sse", mount_path))

    async def run_streamable_http_async(self) -> None:
        self.calls.append(("streamable-http", None))


def test_stdio_transport_runs_the_stdio_loop() -> None:
    server = RecordingTransportServer()

    asyncio.run(run_mcp_server_async(server, transport="stdio"))

    assert server.calls == [("stdio", None)]


def test_sse_transport_forwards_the_mount_path() -> None:
    server = RecordingTransportServer()

    asyncio.run(run_mcp_server_async(server, transport="sse", mount_path="/enji"))

    assert server.calls == [("sse", "/enji")]


def test_streamable_http_transport_runs_the_streamable_loop() -> None:
    server = RecordingTransportServer()

    asyncio.run(run_mcp_server_async(server, transport="streamable-http"))

    assert server.calls == [("streamable-http", None)]


def test_an_unknown_transport_is_refused_before_any_loop_starts() -> None:
    server = RecordingTransportServer()

    with pytest.raises(ValueError, match="Unknown transport: gopher"):
        asyncio.run(run_mcp_server_async(server, transport=cast(server_module.McpTransport, "gopher")))

    assert server.calls == []
