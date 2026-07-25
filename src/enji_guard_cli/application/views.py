"""View primitives shared by more than one facade.

These are application-owned presentation types, not aliases of domain types.
A domain object is *mapped* onto a view; the view carries only strings,
numbers and other views, so a delivery surface can render it without naming a
bounded context.
"""

from dataclasses import dataclass

from enji_guard_cli.portfolio.models import RepositoryIdentity


@dataclass(frozen=True, slots=True)
class RepositoryIdentityView:
    """How the operator names one repository.

    ``provider`` is the plain provider name rather than the domain enum, so
    nothing downstream has to know the enum exists.
    """

    provider: str
    locator: str
    host: str

    @property
    def selector(self) -> str:
        """The ``provider@host:locator`` selector operators type and read."""
        return f"{self.provider}@{self.host}:{self.locator}"


def repository_identity_view(identity: RepositoryIdentity) -> RepositoryIdentityView:
    return RepositoryIdentityView(provider=identity.provider.value, locator=identity.locator, host=identity.host)


__all__ = ["RepositoryIdentityView", "repository_identity_view"]
