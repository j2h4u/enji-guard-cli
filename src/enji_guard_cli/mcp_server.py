import asyncio
import inspect
from pathlib import Path
from typing import Literal, TypedDict, cast

from mcp.server.fastmcp import FastMCP

from enji_guard_cli.audits import AuditAlias, AuditPayload
from enji_guard_cli.core import (
    REPORTS_LIST_DEFAULT_SELECTOR,
    OperationName,
    OperationResult,
    access_async_operation,
    auth_status_async_operation,
    reports_list_async_operation,
    resolve_operation_result,
    resolve_operation_spec,
)
from enji_guard_cli.settings import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT

type JsonCommandResult = OperationResult
type McpTransport = Literal["stdio", "sse", "streamable-http"]

CATALOG_AUDITS_OPERATION = resolve_operation_spec(OperationName.CATALOG_AUDITS)
CATALOG_AUDIT_OPERATION = resolve_operation_spec(OperationName.CATALOG_AUDIT)
ACCESS_OPERATION = resolve_operation_spec(OperationName.ACCESS)
REPORTS_LIST_OPERATION = resolve_operation_spec(OperationName.REPORTS_LIST)
AUTH_STATUS_OPERATION = resolve_operation_spec(OperationName.AUTH_STATUS)
MCP_TOOL_NAMES_BY_OPERATION = {
    OperationName.CATALOG_AUDITS: "enji_catalog_audits",
    OperationName.CATALOG_AUDIT: "enji_catalog_audit",
    OperationName.ACCESS: "enji_access",
    OperationName.REPORTS_LIST: "enji_reports_list",
    OperationName.AUTH_STATUS: "enji_auth_status",
}

get_audit_catalog = CATALOG_AUDITS_OPERATION.execute
get_resolve_audit = CATALOG_AUDIT_OPERATION.execute
get_access = access_async_operation
get_reports_list = reports_list_async_operation
get_auth_status = auth_status_async_operation


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
    if transport == "stdio":
        await server.run_stdio_async()
    elif transport == "sse":
        await server.run_sse_async(mount_path)
    elif transport == "streamable-http":
        await server.run_streamable_http_async()
    else:
        raise ValueError(f"Unknown transport: {transport}")


def run_mcp_server(
    server: FastMCP,
    *,
    transport: McpTransport = "stdio",
    mount_path: str | None = None,
) -> None:
    asyncio.run(run_mcp_server_async(server, transport=transport, mount_path=mount_path))


def create_mcp_server(host: str = DEFAULT_HTTP_HOST, port: int = DEFAULT_HTTP_PORT) -> FastMCP:
    server = FastMCP(
        name="enji-guard-cli",
        instructions=(
            "Thin MCP surface for local Enji Guard catalog metadata, report access, report readiness metadata, "
            "and stored authentication status."
        ),
        host=host,
        port=port,
    )

    @server.tool(
        name=MCP_TOOL_NAMES_BY_OPERATION[OperationName.CATALOG_AUDITS],
        description=CATALOG_AUDITS_OPERATION.summary,
        structured_output=True,
    )
    def catalog_audits() -> CatalogAuditsPayload:
        return {"audits": cast(list[AuditPayload], resolve_operation_result(get_audit_catalog()))}

    @server.tool(
        name=MCP_TOOL_NAMES_BY_OPERATION[OperationName.CATALOG_AUDIT],
        description=CATALOG_AUDIT_OPERATION.summary,
        structured_output=True,
    )
    def catalog_audit(audit: AuditAlias) -> AuditPayload:
        return cast(AuditPayload, resolve_operation_result(get_resolve_audit(audit)))

    @server.tool(
        name=MCP_TOOL_NAMES_BY_OPERATION[OperationName.ACCESS],
        description=ACCESS_OPERATION.summary,
        structured_output=True,
    )
    async def access() -> dict[str, object]:
        return await _resolve_operation_result_async(get_access())

    @server.tool(
        name=MCP_TOOL_NAMES_BY_OPERATION[OperationName.REPORTS_LIST],
        description=REPORTS_LIST_OPERATION.summary,
        structured_output=True,
    )
    async def reports_list(
        selector: str = REPORTS_LIST_DEFAULT_SELECTOR,
    ) -> dict[str, object]:
        return await _resolve_operation_result_async(_invoke_reports_list(selector=selector))

    @server.tool(
        name=MCP_TOOL_NAMES_BY_OPERATION[OperationName.AUTH_STATUS],
        description=AUTH_STATUS_OPERATION.summary,
        structured_output=True,
    )
    async def auth_status(auth_file: str | None = None) -> dict[str, object]:
        target = Path(auth_file).expanduser() if auth_file is not None else None
        return await _resolve_operation_result_async(get_auth_status(target))

    return server


server = create_mcp_server()
