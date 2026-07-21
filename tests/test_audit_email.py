from enji_guard_cli.audit.email import list_for_targets
from enji_guard_cli.audit.ports import AuditEmailPreference, AuditEmailPreferenceUpdate
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.portfolio.models import RepositoryRef
from enji_guard_cli.settings import FanoutSettings


class EmailGateway:
    def get_email_preferences(self, repo_id: str, audit_key: str) -> AuditEmailPreference:
        return AuditEmailPreference(audit_key, repo_id == "repo-1", False)

    def set_email_preference(
        self, repo_id: str, audit_key: str, update: AuditEmailPreferenceUpdate
    ) -> AuditEmailPreference:
        raise AssertionError((repo_id, audit_key, update))


def test_email_listing_preserves_target_and_audit_order() -> None:
    targets = (
        RepositoryRef("repo-1", "project", "Project", "acme/one"),
        RepositoryRef("repo-2", "project", "Project", "acme/two"),
    )

    result = list_for_targets(
        targets,
        ("audit.security", "audit.tests"),
        EmailGateway(),
        BoundedFanout(FanoutSettings(max_concurrency=4)),
    )

    assert tuple(target for target, _preferences in result) == targets
    assert tuple(item.audit_key for item in result[0][1]) == ("audit.security", "audit.tests")
    assert tuple(item.manual for item in result[0][1]) == (True, True)
    assert tuple(item.manual for item in result[1][1]) == (False, False)
