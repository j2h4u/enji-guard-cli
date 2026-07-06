from enji_guard_cli.audits import AuditAlias, ReportAuditAlias


def report_audit(audit: ReportAuditAlias) -> AuditAlias:
    return AuditAlias(audit.value)


def report_audits(audits: list[ReportAuditAlias]) -> list[AuditAlias]:
    return [report_audit(audit) for audit in audits]
