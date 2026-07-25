"""Application layer: one cohesive facade per operator domain.

The cross-cutting machinery — action execution, exception translation, and
exit codes — lives in :mod:`enji_guard_cli.application.execution` and
:mod:`enji_guard_cli.application.errors`, and every facade shares it.  Delivery
surfaces depend on the facades they actually use.
"""

from enji_guard_cli.application.audit import AuditFacade, AuditReconFactory, AuditReconService
from enji_guard_cli.application.auth import AuthFacade
from enji_guard_cli.application.catalog import AuditCatalogService
from enji_guard_cli.application.errors import ApplicationAuthError, ApplicationCommandError, exit_code_for_error
from enji_guard_cli.application.execution import (
    ApplicationCatalogChange,
    ApplicationLifecyclePort,
    ApplicationResult,
    ApplicationRunner,
    CatalogObservationScope,
)
from enji_guard_cli.application.facade import Application
from enji_guard_cli.application.gitlab import (
    GitLabCredentialPageView,
    GitLabCredentialsView,
    GitLabCredentialView,
    GitLabFacade,
    GitLabProjectPageView,
    GitLabProjectsView,
    GitLabProjectView,
    GitLabScopeView,
)
from enji_guard_cli.application.portfolio import PortfolioFacade
from enji_guard_cli.application.projects import AuditProjectSource
from enji_guard_cli.application.subscriptions import (
    AutofixListing,
    AutofixListingItem,
    AutofixWriteScope,
    ScheduleListing,
    SubscriptionsFacade,
)
from enji_guard_cli.application.views import RepositoryIdentityView

__all__ = [
    "Application",
    "ApplicationAuthError",
    "ApplicationCatalogChange",
    "ApplicationCommandError",
    "ApplicationLifecyclePort",
    "ApplicationResult",
    "ApplicationRunner",
    "AuditCatalogService",
    "AuditFacade",
    "AuditProjectSource",
    "AuditReconFactory",
    "AuditReconService",
    "AuthFacade",
    "AutofixListing",
    "AutofixListingItem",
    "AutofixWriteScope",
    "CatalogObservationScope",
    "GitLabCredentialPageView",
    "GitLabCredentialView",
    "GitLabCredentialsView",
    "GitLabFacade",
    "GitLabProjectPageView",
    "GitLabProjectView",
    "GitLabProjectsView",
    "GitLabScopeView",
    "PortfolioFacade",
    "RepositoryIdentityView",
    "ScheduleListing",
    "SubscriptionsFacade",
    "exit_code_for_error",
]
