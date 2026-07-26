"""Typed failures exposed by the Audit bounded context."""


class AuditNotFoundError(LookupError):
    """A requested audit, task, or artifact does not exist/readably exist."""


class AuditUpstreamError(RuntimeError):
    """The upstream audit service could not provide a usable response."""


class AuditMalformedError(ValueError):
    """The upstream response violated the Audit contract."""


class AuditRepositoryUnusableError(ValueError):
    """The target repository cannot carry any audit run.

    Repository-scoped: the same failure applies to every audit selected for
    that repository, so it aborts the whole batch with one message instead of
    marking each audit ``failed`` for the same reason.
    """


class AuditActionUnusableError(ValueError):
    """One catalog action lacks the metadata an audit run needs.

    Action-scoped: the remaining audits in the batch are unaffected, so only
    this audit is reported ``failed`` and the batch continues.
    """
