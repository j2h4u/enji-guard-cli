"""Typed failures exposed by the Portfolio bounded context."""


class PortfolioNotFoundError(LookupError, ValueError):
    """A project or repository selector has no matching target."""


class PortfolioUpstreamError(RuntimeError):
    """The upstream portfolio service could not complete an operation."""


class PortfolioMalformedError(ValueError):
    """An upstream portfolio response violated its typed contract."""
