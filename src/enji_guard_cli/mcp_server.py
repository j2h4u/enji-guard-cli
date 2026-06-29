import asyncio
import inspect
from contextlib import suppress
from pathlib import Path
from typing import Literal, TypedDict, cast

from mcp.server.fastmcp import FastMCP

from enji_guard_cli.auth import AuthStatusPayload, auth_status_async, start_auto_refresh_task
from enji_guard_cli.core import (
    REPORTS_LIST_DEFAULT_SELECTOR,
    AuditAlias,
    AuditPayload,
    OperationName,
    OperationResult,
    resolve_operation_result,
    resolve_operation_spec,
)
from enji_guard_cli.enji_api import access_async, reports_list_async

type JsonCommandResult = OperationResult
type McpTransport = Literal["stdio", "sse", "streamable-http"]

CATALOG_AUDITS_OPERATION = resolve_operation_spec(OperationName.CATALOG_AUDITS)
CATALOG_AUDIT_OPERATION = resolve_operation_spec(OperationName.CATALOG_AUDIT)
ACCESS_OPERATION = resolve_operation_spec(OperationName.ACCESS)
REPORTS_LIST_OPERATION = resolve_operation_spec(OperationName.REPORTS_LIST)
AUTH_STATUS_OPERATION = resolve_operation_spec(OperationName.AUTH_STATUS)

get_audit_catalog = CATALOG_AUDITS_OPERATION.execute
get_resolve_audit = CATALOG_AUDIT_OPERATION.execute
get_access = access_async
get_reports_list = reports_list_async
get_auth_status = auth_status_async


class CatalogAuditsPayload(TypedDict):
    audits: list[AuditPayload]


async def _resolve_operation_result_async[T](result: T | JsonCommandResult) -> T:
    if inspect.isawaitable(result):
        return cast(T, await result)
    return cast(T, result)


def _invoke_reports_list(selector: str) -> JsonCommandResult:
    return get_reports_list(selector=selector)


async def run_mcp_server_async(
    server: FastMCP,
    *,
    transport: McpTransport = "stdio",
    mount_path: str | None = None,
) -> None:
    auto_refresh_task = start_auto_refresh_task()
    try:
        if transport == "stdio":
            await server.run_stdio_async()
        elif transport == "sse":
            await server.run_sse_async(mount_path)
        elif transport == "streamable-http":
            await server.run_streamable_http_async()
        else:
            raise ValueError(f"Unknown transport: {transport}")
    finally:
        await _cancel_auto_refresh_task(auto_refresh_task)


def run_mcp_server(
    server: FastMCP,
    *,
    transport: McpTransport = "stdio",
    mount_path: str | None = None,
) -> None:
    asyncio.run(run_mcp_server_async(server, transport=transport, mount_path=mount_path))


async def _cancel_auto_refresh_task(auto_refresh_task: asyncio.Task[None] | None) -> None:
    if auto_refresh_task is None:
        return
    auto_refresh_task.cancel()
    with suppress(asyncio.CancelledError):
        await auto_refresh_task


def create_mcp_server(host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    server = FastMCP(
        name="enji-guard-cli",
        instructions=(
            "Thin MCP surface for local Enji Guard catalog metadata, report access, report listings, "
            "and stored authentication status."
        ),
        host=host,
        port=port,
    )

    @server.tool(
        name=CATALOG_AUDITS_OPERATION.mcp_tool,
        description=CATALOG_AUDITS_OPERATION.summary,
        structured_output=True,
    )
    def catalog_audits() -> CatalogAuditsPayload:
        return {"audits": cast(list[AuditPayload], resolve_operation_result(get_audit_catalog()))}

    @server.tool(
        name=CATALOG_AUDIT_OPERATION.mcp_tool,
        description=CATALOG_AUDIT_OPERATION.summary,
        structured_output=True,
    )
    def catalog_audit(audit: AuditAlias) -> AuditPayload:
        return cast(AuditPayload, resolve_operation_result(get_resolve_audit(audit)))

    @server.tool(
        name=ACCESS_OPERATION.mcp_tool,
        description=ACCESS_OPERATION.summary,
        structured_output=True,
    )
    async def access() -> dict[str, object]:
        return await _resolve_operation_result_async(get_access())

    @server.tool(
        name=REPORTS_LIST_OPERATION.mcp_tool,
        description=REPORTS_LIST_OPERATION.summary,
        structured_output=True,
    )
    async def reports_list(
        selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    ) -> dict[str, object]:
        return await _resolve_operation_result_async(_invoke_reports_list(selector=selector))

    @server.tool(
        name=AUTH_STATUS_OPERATION.mcp_tool,
        description=AUTH_STATUS_OPERATION.summary,
        structured_output=True,
    )
    async def auth_status(auth_file: str | None = None) -> AuthStatusPayload:
        target = Path(auth_file).expanduser() if auth_file is not None else None
        return await _resolve_operation_result_async(get_auth_status(target))

    return server


server = create_mcp_server()
