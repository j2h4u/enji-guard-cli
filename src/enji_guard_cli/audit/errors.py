"""Typed failures exposed by the Audit bounded context."""


class AuditNotFoundError(LookupError):
    """A requested audit, task, or artifact does not exist/readably exist."""


class AuditUpstreamError(RuntimeError):
    """The upstream audit service could not provide a usable response."""


class AuditMalformedError(ValueError):
    """The upstream response violated the Audit contract."""
