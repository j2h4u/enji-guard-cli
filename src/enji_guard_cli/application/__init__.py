"""Application layer: one cohesive facade per operator domain.

The cross-cutting machinery — action execution, exception translation, and
exit codes — lives in :mod:`enji_guard_cli.application.execution` and
:mod:`enji_guard_cli.application.errors`, and every facade shares it.  Delivery
surfaces depend on the facades they actually use.
"""

from enji_guard_cli.application.audit import AuditFacade, AuditReconFactory, AuditReconService
from enji_guard_cli.application.audit_views import (
    AuditArtifactView,
    AuditCurrentHeadView,
    AuditFreshnessView,
    AuditNewerRunView,
    AuditReadItemView,
    AuditReadView,
    AuditRunView,
    AuditStatusItemView,
    AuditStatusView,
    AuditSummaryItemView,
    AuditSummaryView,
    AuditWaitView,
)
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
    GitLabProjectsRequest,
    GitLabProjectsView,
    GitLabProjectView,
    GitLabScopeView,
)
from enji_guard_cli.application.mutations import (
    BatchMutationResult,
    MutationDecision,
    MutationOutcome,
    MutationReason,
    MutationTargetView,
)
from enji_guard_cli.application.portfolio import PortfolioFacade
from enji_guard_cli.application.portfolio_views import (
    AccountPreferencesView,
    PortfolioActiveRunView,
    PortfolioAuditStatusView,
    PortfolioOverviewView,
    ProjectOverviewView,
    ProjectRefView,
    ProjectSettingsView,
    RepositoryOverviewView,
    RepositoryRefView,
    RepositoryStatusView,
)
from enji_guard_cli.application.projects import AuditProjectSource
from enji_guard_cli.application.subscription_views import (
    AuditScheduleView,
    ImprovementDefinitionView,
    ImprovementJobView,
)
from enji_guard_cli.application.subscriptions import (
    AUDIT_CADENCES,
    EmailPreferencesWriteRequest,
    ImprovementJobListing,
    ImprovementJobListingItem,
    ImprovementJobWriteRequest,
    ScheduleListing,
    ScheduleWriteRequest,
    SubscriptionsFacade,
    SubscriptionWriteScope,
)
from enji_guard_cli.application.views import RepositoryIdentityView

__all__ = [
    "AUDIT_CADENCES",
    "AccountPreferencesView",
    "Application",
    "ApplicationAuthError",
    "ApplicationCatalogChange",
    "ApplicationCommandError",
    "ApplicationLifecyclePort",
    "ApplicationResult",
    "ApplicationRunner",
    "AuditArtifactView",
    "AuditCatalogService",
    "AuditCurrentHeadView",
    "AuditFacade",
    "AuditFreshnessView",
    "AuditNewerRunView",
    "AuditProjectSource",
    "AuditReadItemView",
    "AuditReadView",
    "AuditReconFactory",
    "AuditReconService",
    "AuditRunView",
    "AuditScheduleView",
    "AuditStatusItemView",
    "AuditStatusView",
    "AuditSummaryItemView",
    "AuditSummaryView",
    "AuditWaitView",
    "AuthFacade",
    "BatchMutationResult",
    "CatalogObservationScope",
    "EmailPreferencesWriteRequest",
    "GitLabCredentialPageView",
    "GitLabCredentialView",
    "GitLabCredentialsView",
    "GitLabFacade",
    "GitLabProjectPageView",
    "GitLabProjectView",
    "GitLabProjectsRequest",
    "GitLabProjectsView",
    "GitLabScopeView",
    "ImprovementDefinitionView",
    "ImprovementJobListing",
    "ImprovementJobListingItem",
    "ImprovementJobView",
    "ImprovementJobWriteRequest",
    "MutationDecision",
    "MutationOutcome",
    "MutationReason",
    "MutationTargetView",
    "PortfolioActiveRunView",
    "PortfolioAuditStatusView",
    "PortfolioFacade",
    "PortfolioOverviewView",
    "ProjectOverviewView",
    "ProjectRefView",
    "ProjectSettingsView",
    "RepositoryIdentityView",
    "RepositoryOverviewView",
    "RepositoryRefView",
    "RepositoryStatusView",
    "ScheduleListing",
    "ScheduleWriteRequest",
    "SubscriptionWriteScope",
    "SubscriptionsFacade",
    "exit_code_for_error",
]
