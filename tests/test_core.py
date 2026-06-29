from enji_guard_cli.core import (
    AUDITS,
    OPERATION_SPECS,
    AuditAlias,
    OperationName,
    audit_catalog,
    operation_catalog,
    resolve_audit,
    resolve_operation,
    resolve_operation_spec,
)


def test_operation_catalog_is_one_to_one_across_core_cli_and_mcp_surfaces() -> None:
    catalog = operation_catalog()

    assert [entry["name"] for entry in catalog] == [spec.name.value for spec in OPERATION_SPECS]
    assert len({entry["name"] for entry in catalog}) == len(catalog)
    assert len({entry["cli_command"] for entry in catalog}) == len(catalog)
    assert len({entry["mcp_tool"] for entry in catalog}) == len(catalog)


def test_operation_catalog_includes_catalog_access_reports_and_auth_specs() -> None:
    assert operation_catalog() == [
        {
            "name": OperationName.CATALOG_AUDITS.value,
            "cli_command": "catalog audits",
            "mcp_tool": "enji_catalog_audits",
            "summary": "List the canonical Enji Guard audit catalog.",
        },
        {
            "name": OperationName.CATALOG_AUDIT.value,
            "cli_command": "catalog audit",
            "mcp_tool": "enji_catalog_audit",
            "summary": "Resolve one canonical Enji Guard audit alias.",
        },
        {
            "name": OperationName.ACCESS.value,
            "cli_command": "access",
            "mcp_tool": "enji_access",
            "summary": "Return Enji Guard plan, limits, and schedule access metadata.",
        },
        {
            "name": OperationName.REPORTS_LIST.value,
            "cli_command": "report list",
            "mcp_tool": "enji_reports_list",
            "summary": "List compact Enji Guard report summaries across repositories.",
        },
        {
            "name": OperationName.AUTH_STATUS.value,
            "cli_command": "auth status",
            "mcp_tool": "enji_auth_status",
            "summary": "Report whether stored Enji Guard credentials are authenticated.",
        },
    ]


def test_resolve_operation_returns_new_access_and_reports_specs() -> None:
    assert resolve_operation(OperationName.ACCESS) == {
        "name": "access",
        "cli_command": "access",
        "mcp_tool": "enji_access",
        "summary": "Return Enji Guard plan, limits, and schedule access metadata.",
    }
    assert resolve_operation(OperationName.REPORTS_LIST) == {
        "name": "reports_list",
        "cli_command": "report list",
        "mcp_tool": "enji_reports_list",
        "summary": "List compact Enji Guard report summaries across repositories.",
    }


def test_operation_specs_are_executable_bindings() -> None:
    assert resolve_operation_spec(OperationName.CATALOG_AUDITS).execute() == audit_catalog()
    assert resolve_operation_spec(OperationName.CATALOG_AUDIT).execute(AuditAlias.DEPS) == resolve_audit(
        AuditAlias.DEPS
    )
    assert callable(resolve_operation_spec(OperationName.ACCESS).execute)
    assert callable(resolve_operation_spec(OperationName.REPORTS_LIST).execute)
    assert callable(resolve_operation_spec(OperationName.AUTH_STATUS).execute)


def test_audit_catalog_is_derived_from_canonical_audit_definitions() -> None:
    assert len(audit_catalog()) == len(AUDITS)
    assert resolve_audit(AuditAlias.RECON) == {
        "alias": "recon",
        "label": "Recon",
        "route_slug": None,
        "job_kind": None,
        "action_key": "audit.recon",
    }
