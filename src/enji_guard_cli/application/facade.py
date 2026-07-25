"""The set of facades one CLI process holds.

This is a composition record, not a facade of facades: it declares no
use-cases of its own, so a delivery surface that needs a single domain takes
that facade instead of this record.
"""

from dataclasses import dataclass

from enji_guard_cli.application.audit import AuditFacade
from enji_guard_cli.application.auth import AuthFacade
from enji_guard_cli.application.catalog import AuditCatalogService
from enji_guard_cli.application.execution import ApplicationRunner
from enji_guard_cli.application.gitlab import GitLabFacade
from enji_guard_cli.application.portfolio import PortfolioFacade
from enji_guard_cli.application.subscriptions import SubscriptionsFacade


@dataclass(frozen=True, slots=True)
class Application:
    """Every domain facade the operator CLI can reach in one process."""

    runner: ApplicationRunner
    catalog: AuditCatalogService
    auth: AuthFacade
    audit: AuditFacade
    subscriptions: SubscriptionsFacade
    portfolio: PortfolioFacade
    gitlab: GitLabFacade


__all__ = ["Application"]
