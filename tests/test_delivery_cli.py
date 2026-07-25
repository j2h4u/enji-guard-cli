"""CLI delivery over the real facade tree.

Every command test here drives the same facades production composes and
asserts on the *port* calls that resulted, not on which facade method the CLI
named.  Only the port view distinguishes a facade that forwarded its arguments
from one that translated them wrongly.
"""

import importlib
import json
from typing import cast

import pytest
import typer
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from application_builder import (
    PETS,
    ApplicationStubs,
    RecordingAuditGateway,
    RecordingAuthSession,
    RecordingGitLabGateway,
    RecordingPortfolioGateway,
    RecordingTargetService,
    WriteTargetsCall,
    recording_application,
    repository,
)
from enji_guard_cli.application import ApplicationAuthError, ApplicationCommandError
from enji_guard_cli.audit.ports import (
    AuditArtifact,
    AuditAutofixJob,
    AuditCatalogAction,
    AuditCatalogAutofix,
    AuditCatalogResult,
    AuditReportRef,
    AuditRerunState,
    AuditRun,
    AuditSchedule,
    AuditTaskLink,
)
from enji_guard_cli.auth_session.api import AuthError
from enji_guard_cli.delivery.cli.app import _command_exit_code, _json, _run, app
from enji_guard_cli.delivery.cli.presentation import FIELDS_PRESENTATION, render_fields
from enji_guard_cli.delivery.cli.presenters import operation_text
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.gitlab.models import (
    GitLabCredential,
    GitLabProjectPage,
    GitLabProjectsQuery,
    GitLabProjectsResult,
    GitLabScope,
)
from enji_guard_cli.portfolio.models import ProjectDetail
from enji_guard_cli.runtime_observability.supervisor import RuntimeServiceOptions

cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")

REPO = "github@github.com:acme/cat"
REPORT_COMPLETED_AT = "2026-07-20T00:00:00Z"


def _catalog(*published: AuditCatalogAction, autofixes: tuple[AuditCatalogAutofix, ...] = ()) -> AuditCatalogResult:
    """Build a catalog around the mandatory recon action."""
    recon = AuditCatalogAction(
        "audit.recon", "Recon", "workflow", "draft", None, "recon", "runbook-recon", "recon", "1"
    )
    return AuditCatalogResult(actions=(recon, *published), autofixes=autofixes)


def _audit(selector: str, title: str) -> AuditCatalogAction:
    return AuditCatalogAction(
        f"audit.{selector}", title, "audit", "published", selector, "audit", f"runbook-{selector}", selector, "1"
    )


SECURITY_ONLY = _catalog(_audit("security", "Security"))


class Ports:
    """Every port one CLI invocation can reach, each recording its own calls."""

    def __init__(
        self,
        *,
        audit: RecordingAuditGateway | None = None,
        portfolio: RecordingPortfolioGateway | None = None,
        targets: RecordingTargetService | None = None,
        gitlab: RecordingGitLabGateway | None = None,
        auth: RecordingAuthSession | None = None,
    ) -> None:
        self.audit = audit or RecordingAuditGateway()
        self.portfolio = portfolio or RecordingPortfolioGateway((ProjectDetail(PETS, (repository(),)),))
        self.targets = targets or RecordingTargetService()
        self.auth = auth or RecordingAuthSession()
        self.gitlab = gitlab

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        application = recording_application(
            audit=self.audit,
            portfolio=self.portfolio,
            targets=self.targets,
            auth=self.auth,
            gitlab=self.gitlab,
        )
        monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: application)


@pytest.fixture
def ports(monkeypatch: pytest.MonkeyPatch) -> Ports:
    installed = Ports()
    installed.install(monkeypatch)
    return installed


def test_operator_command_tree_uses_audit_vocabulary() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("auth", "project", "repo", "recon", "audit", "schedule", "improvement-jobs", "email", "language"):
        assert command in result.stdout


def test_root_without_arguments_shows_agent_mental_model() -> None:
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0
    assert "Mental model" in result.stdout
    assert "status REPO" in result.stdout
    assert "one fresh snapshot" in result.stdout
    assert "wait REPO" in result.stdout
    assert "do not use short timeouts as refresh" in result.stdout


def test_audit_help_warns_that_wait_is_not_refresh() -> None:
    result = CliRunner().invoke(app, ["audit", "--help"])
    assert result.exit_code == 0
    assert "Use status REPO as the first readiness check" in result.stdout
    assert "wait is a real blocking wait" in result.stdout
    assert "not a" in result.stdout
    assert "refresh" in result.stdout


def test_audit_read_and_summary_are_public_commands() -> None:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    audit = root.commands["audit"]
    assert isinstance(audit, TyperGroup)
    assert set(audit.commands) >= {"read", "summary", "start"}
    runner = CliRunner()
    # Keep invocation as a reachability smoke check; command membership is the contract.
    assert runner.invoke(app, ["audit", "read", "--help"]).exit_code == 0
    assert runner.invoke(app, ["audit", "summary", "--help"]).exit_code == 0


def test_run_defaults_to_long_lived_http_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_service(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "run_service", fake_run_service)

    result = CliRunner().invoke(app, ["run", "--port", "18080"])

    assert result.exit_code == 0
    options = cast(RuntimeServiceOptions, captured["options"])
    assert options.transport == "streamable-http"
    assert options.host == "127.0.0.1"
    assert options.port == 18080


def test_run_keeps_stdio_as_an_explicit_interactive_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_service(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "run_service", fake_run_service)

    result = CliRunner().invoke(app, ["run", "--transport", "stdio"])

    assert result.exit_code == 0
    assert cast(RuntimeServiceOptions, captured["options"]).transport == "stdio"


def test_audit_start_resolves_the_selector_and_starts_the_named_audit(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["audit", "start", REPO, "security", "--project", "Pets", "--json"])

    assert result.exit_code == 0
    assert result.exception is None
    payload = cast(dict[str, object], json.loads(result.stdout))
    assert payload["repo_id"] == "r1"
    assert payload["project_id"] == "p1"
    assert [(call.selector, call.project) for call in ports.targets.resolve_repository_calls] == [(REPO, "Pets")]
    started = ports.audit.started
    assert [(item.repo_id, item.project_id, item.action_key) for item in started] == [("r1", "p1", "audit.security")]


def test_gitlab_projects_maps_all_query_options_and_emits_result(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_query = GitLabProjectsQuery(
        credential_id="cred-42",
        search="backend",
        page=3,
        per_page=17,
        all_pages=True,
        scope_type="group",
        scope_owner="acme",
    )
    payload = GitLabProjectsResult(
        scope=GitLabScope(scope_type="group", scope_owner="acme"),
        credential=GitLabCredential(
            id="cred-42",
            name="automation",
            credential_type="cookie",
            provider="gitlab",
            scope_type="group",
            scope_owner="acme",
            status="ready",
            last_error=None,
            expires_at=None,
            git_host="gitlab.com",
            api_base_url="https://gitlab.com/api/v4",
            gitlab_health_reason=None,
        ),
        projects=(),
        pagination=GitLabProjectPage(page=3, per_page=17, next_page=None),
    )
    gitlab = RecordingGitLabGateway(projects=payload)
    Ports(gitlab=gitlab).install(monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "gitlab",
            "projects",
            "--credential-id",
            "cred-42",
            "--search",
            "backend",
            "--page",
            "3",
            "--per-page",
            "17",
            "--all-pages",
            "--scope-type",
            "group",
            "--scope-owner",
            "acme",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert result.exception is None
    assert gitlab.queries == [expected_query]
    rendered = cast(dict[str, object], json.loads(result.stdout))
    assert cast(dict[str, object], rendered["credential"])["id"] == "cred-42"
    assert "\x1b" not in result.stdout


def test_project_settings_reads_the_selected_project_and_account_preferences(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["project", "settings", "--project", "Pets", "--json"])

    assert result.exit_code == 0
    assert ports.targets.resolve_project_calls == ["Pets"]
    payload = cast(dict[str, object], json.loads(result.stdout))
    assert cast(dict[str, object], payload["project"])["project_id"] == "p1"
    assert cast(dict[str, object], payload["account_preferences"])["language"] == "en"


def test_access_is_served_by_the_portfolio_gateway(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["access", "--json"])

    assert result.exit_code == 0
    payload = cast(dict[str, object], json.loads(result.stdout))
    assert payload["full_access"] is True
    assert payload["group"] == "free"


def test_portfolio_status_applies_the_requested_repository_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--sort`` must reach the overview assembly, not just the command line."""
    strong = repository("acme/strong", repo_id="r-strong", scores={"tests": 90, "vulns": 80})
    weak = repository("acme/weak", repo_id="r-weak", scores={"tests": 70, "vulns": 10})
    portfolio = RecordingPortfolioGateway((ProjectDetail(PETS, (strong, weak)),))
    Ports(portfolio=portfolio).install(monkeypatch)

    result = CliRunner().invoke(app, ["--project", "Pets", "status", "--sort", "weakest", "--json"])

    assert result.exit_code == 0
    payload = cast(dict[str, object], json.loads(result.stdout))
    projects = cast(list[dict[str, object]], payload["projects"])
    repositories = cast(list[dict[str, object]], projects[0]["repositories"])
    locators = [
        cast(dict[str, object], cast(dict[str, object], item["repository"])["identity"])["locator"]
        for item in repositories
    ]
    assert locators == ["acme/weak", "acme/strong"]


def test_status_for_one_repository_keeps_detailed_status(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["--project", "Pets", "status", REPO, "--json"])

    assert result.exit_code == 0
    payload = cast(list[dict[str, object]], json.loads(result.stdout))
    repository_payload = cast(dict[str, object], payload[0]["repository"])
    identity = cast(dict[str, object], repository_payload["identity"])
    summary = cast(dict[str, object], cast(dict[str, object], payload[0]["audit"])["summary"])
    assert identity["locator"] == "acme/cat"
    assert {item["audit_key"] for item in cast(list[dict[str, object]], summary["items"])} == {
        "audit.security",
        "audit.tests",
    }


def test_portfolio_text_is_compact_and_scenario_oriented(monkeypatch: pytest.MonkeyPatch) -> None:
    cat = repository("acme/cat", recon_done=True, scores={"tests": 80, "vulns": 40})
    Ports(portfolio=RecordingPortfolioGateway((ProjectDetail(PETS, (cat,)),))).install(monkeypatch)

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Pets" in result.stdout
    assert "github@github.com:acme/cat  weakest=40 overall=60.0 recon=ready active=0" in result.stdout
    assert '"projects"' not in result.stdout


def test_repository_status_text_is_compact_and_does_not_dump_json(monkeypatch: pytest.MonkeyPatch) -> None:
    audit = RecordingAuditGateway(
        catalog=SECURITY_ONLY,
        task_links={"r1": (AuditTaskLink("t1", "audit.security", "completed", completed_at=REPORT_COMPLETED_AT),)},
        rerun_state=AuditRerunState("head", "head", True, "t1", {"audit.security": "head"}),
    )
    Ports(audit=audit).install(monkeypatch)

    result = CliRunner().invoke(app, ["status", REPO])

    assert result.exit_code == 0
    assert "audits: total=1 ready=1 active=0 stale=0 failed=0" in result.stdout
    assert "security  state=ready freshness=fresh" in result.stdout
    assert '"audit_key"' not in result.stdout


def _readable_security_gateway(*, active_runs: tuple[AuditRun, ...] = ()) -> RecordingAuditGateway:
    """One published audit with exactly one completed, readable report."""
    return RecordingAuditGateway(
        catalog=SECURITY_ONLY,
        task_links={"r1": (AuditTaskLink("t1", "audit.security", "completed", completed_at=REPORT_COMPLETED_AT),)},
        rerun_state=AuditRerunState("head", "head", True, "t1", {"audit.security": "head"}),
        reports={("r1", "security"): (AuditReportRef("t1", REPORT_COMPLETED_AT, None, True),)},
        artifacts={
            ("r1", "audit.security"): AuditArtifact(
                "audit.security", "# Security report\n\n- Fix the vulnerable dependency.", 73, REPORT_COMPLETED_AT
            )
        },
        active_runs={"r1": active_runs},
    )


def test_audit_summary_is_compact_in_text_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    Ports(audit=_readable_security_gateway()).install(monkeypatch)

    text_result = CliRunner().invoke(app, ["audit", "summary", REPO])
    json_result = CliRunner().invoke(app, ["audit", "summary", REPO, "--json"])

    assert text_result.exit_code == 0
    assert "security  score=73 freshness=fresh" in text_result.stdout
    assert "Fix the vulnerable dependency" not in text_result.stdout
    assert json_result.exit_code == 0
    payload = cast(dict[str, object], json.loads(json_result.stdout))
    audits = cast(list[dict[str, object]], payload["audits"])
    assert payload["repo_id"] == "r1"
    assert audits[0]["score"] == 73
    assert "body" not in audits[0]


def test_audit_read_renders_markdown_for_humans_and_equivalent_json(monkeypatch: pytest.MonkeyPatch) -> None:
    report = "# Security report\n\n- Fix the vulnerable dependency."
    newer = AuditRun("task-new", "audit.security", "running", None, "2026-07-21T00:00:00Z", None)
    Ports(audit=_readable_security_gateway(active_runs=(newer,))).install(monkeypatch)

    text_result = CliRunner().invoke(app, ["audit", "read", REPO, "security"])
    json_result = CliRunner().invoke(app, ["audit", "read", REPO, "security", "--json"])

    assert text_result.exit_code == 0
    assert "repository: r1" in text_result.stdout
    assert "## security" in text_result.stdout
    assert "freshness: fresh" in text_result.stdout
    assert "score: 73" in text_result.stdout
    assert "Report is stale; a newer audit is in progress." in text_result.stdout
    assert report in text_result.stdout
    assert "\\n" not in text_result.stdout
    assert '"audits"' not in text_result.stdout
    assert json_result.exit_code == 0
    rendered = cast(dict[str, object], json.loads(json_result.stdout))
    audits = cast(list[dict[str, object]], rendered["audits"])
    artifact = cast(dict[str, object], audits[0]["artifact"])
    assert rendered["repo_id"] == "r1"
    assert audits[0]["audit_key"] == "audit.security"
    assert artifact["body"] == report
    assert artifact["score"] == 73
    assert cast(dict[str, object], audits[0]["newer_run"])["task_id"] == "task-new"
    assert "Report is stale; a newer audit is in progress." not in json_result.stdout


def test_json_omits_nested_optional_null_fields_but_keeps_top_level_and_list_nulls() -> None:
    assert _json({"present": 1, "missing": None, "nested": {"missing": None, "present": 2}}) == {
        "present": 1,
        "nested": {"present": 2},
    }
    assert _json([None, {"missing": None}]) == [None, {}]
    assert _json(None) is None


def test_json_preserves_semantic_nulls_and_non_null_falsy_values() -> None:
    assert _json(
        {
            "job": None,
            "connected": None,
            "recon_done": None,
            "enabled": None,
            "auto_fix": None,
            "score": None,
            "false": False,
            "zero": 0,
            "empty": [],
        }
    ) == {
        "job": None,
        "connected": None,
        "recon_done": None,
        "enabled": None,
        "auto_fix": None,
        "score": None,
        "false": False,
        "zero": 0,
        "empty": [],
    }


def test_schedule_list_is_one_summary_line_per_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    audit = RecordingAuditGateway(
        schedules={
            "r1": (
                AuditSchedule("audit.security", True, "workdays", None, None, "09:00", "auto", "Asia/Almaty"),
                AuditSchedule("audit.tests", False, "weekly", None, None, "10:00", "user", "UTC"),
            )
        }
    )
    Ports(audit=audit).install(monkeypatch)

    text_result = CliRunner().invoke(app, ["schedule", "list"])
    json_result = CliRunner().invoke(app, ["schedule", "list", "--json"])

    assert text_result.exit_code == 0
    output = text_result.stdout.strip()
    for field in (
        "github@github.com:acme/cat",
        "enabled=1/2",
        "frequency=mixed[security=workdays,tests=weekly]",
        "timezone=mixed[security=Asia/Almaty,tests=UTC]",
        "enabled_state=mixed[security=true,tests=false]",
        "schedule_time=mixed[security=09:00,tests=10:00]",
        "schedule_time_source=mixed[security=auto,tests=user]",
        "disabled=tests",
    ):
        assert field in output
    assert json_result.exit_code == 0
    payload = cast(list[dict[str, object]], json.loads(json_result.stdout))
    identity = cast(dict[str, object], cast(dict[str, object], payload[0]["repository"])["identity"])
    assert identity["locator"] == "acme/cat"
    assert len(cast(list[object], payload[0]["schedules"])) == 2


def test_json_preserves_null_scores_from_typed_repository_dto() -> None:
    rendered = _json(repository("acme/cat", scores={"audit.security": None, "audit.tests": 0}))

    assert isinstance(rendered, dict)
    assert rendered["scores"] == {"audit.security": None, "audit.tests": 0}


AUTOFIX_CATALOG = _catalog(
    _audit("security", "Security"),
    _audit("tests", "Tests"),
    _audit("dependency-hygiene", "Dependencies"),
    autofixes=(
        AuditCatalogAutofix("improvement.vuln-fix", "default", "Vuln fix", None, "rb-1", "published", 1),
        AuditCatalogAutofix("improvement.dependency-update", "default", "Deps", None, "rb-2", "published", 2),
        AuditCatalogAutofix("improvement.test-writing", "default", "Tests", None, "rb-3", "published", 3),
    ),
)

TEST_WRITING_ONLY = _catalog(
    _audit("tests", "Tests"),
    autofixes=(AuditCatalogAutofix("improvement.test-writing", "default", "Tests", None, "rb-3", "published", 1),),
)


def test_improvement_jobs_list_is_one_summary_line_per_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    job = AuditAutofixJob(
        "improvement.test-writing", "default", "test-writing", True, True, frequency="workdays", timezone="UTC"
    )
    Ports(audit=RecordingAuditGateway(catalog=TEST_WRITING_ONLY, autofix_jobs={"r1": (job,)})).install(monkeypatch)

    result = CliRunner().invoke(app, ["improvement-jobs", "list"])

    assert result.exit_code == 0
    output = result.stdout.strip()
    for field in (
        "github@github.com:acme/cat",
        "enabled=1/1",
        "configured=1/1",
        "auto_fix=1/1",
        "supported=test-writing",
        "enabled_state=true",
        "auto_fix_state=true",
        "frequency=workdays",
        "timezone=UTC",
    ):
        assert field in output


def test_improvement_jobs_text_preserves_mixed_dimensions_and_states(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = (
        AuditAutofixJob(
            "improvement.vuln-fix",
            "default",
            "vuln-fix",
            True,
            False,
            frequency="daily",
            days_of_week=("mon",),
            schedule_time="09:00",
            schedule_time_source="auto",
            pentest_mode="off",
        ),
        AuditAutofixJob(
            "improvement.dependency-update",
            "default",
            "dependency-update",
            False,
            None,
            frequency="weekly",
            days_of_week=("fri",),
            schedule_time="10:00",
            schedule_time_source="user",
            pentest_mode="on",
        ),
    )
    Ports(audit=RecordingAuditGateway(catalog=AUTOFIX_CATALOG, autofix_jobs={"r1": jobs})).install(monkeypatch)

    result = CliRunner().invoke(app, ["improvement-jobs", "list"])

    assert result.exit_code == 0
    assert "enabled=1/3 configured=2/3 auto_fix=0/3" in result.stdout
    assert "enabled_state=mixed[vuln-fix=true,dependency-update=false]" in result.stdout
    assert "auto_fix_state=mixed[vuln-fix=false,dependency-update=unset]" in result.stdout
    assert "frequency=mixed[vuln-fix=daily,dependency-update=weekly]" in result.stdout
    assert "days=mixed[vuln-fix=mon,dependency-update=fri]" in result.stdout
    assert "schedule_time=mixed[vuln-fix=09:00,dependency-update=10:00]" in result.stdout
    assert "schedule_time_source=mixed[vuln-fix=auto,dependency-update=user]" in result.stdout
    assert "pentest_mode=mixed[vuln-fix=off,dependency-update=on]" in result.stdout
    assert "unconfigured=test-writing disabled=dependency-update" in result.stdout


def test_improvement_jobs_text_does_not_report_unknown_enabled_state_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = AuditAutofixJob("improvement.test-writing", "default", "test-writing", None, True)
    Ports(audit=RecordingAuditGateway(catalog=TEST_WRITING_ONLY, autofix_jobs={"r1": (job,)})).install(monkeypatch)

    result = CliRunner().invoke(app, ["improvement-jobs", "list"])

    assert result.exit_code == 0
    assert "enabled=0/1" in result.stdout
    assert "disabled=" not in result.stdout
    assert "enabled_unknown=test-writing" in result.stdout


def test_schedule_list_groups_restricted_window_days_by_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _catalog(_audit("security", "Security"), _audit("tests", "Tests"), _audit("deps", "Deps"))
    audit = RecordingAuditGateway(
        catalog=catalog,
        schedules={
            "r1": (
                AuditSchedule("audit.security", True, "daily", None, None, None, "auto", "UTC", ("mon", "wed")),
                AuditSchedule("audit.tests", True, "daily", None, None, None, "auto", "UTC", ("mon", "wed")),
                AuditSchedule("audit.deps", True, "daily", None, None, None, "auto", "UTC", ("fri",)),
            )
        },
    )
    Ports(audit=audit).install(monkeypatch)

    result = CliRunner().invoke(app, ["schedule", "list"])

    assert result.exit_code == 0
    assert "window_days=mon,wed:security,tests|fri:deps" in result.stdout


def test_batch_schedule_write_reaches_every_published_audit_of_the_scope(ports: Ports) -> None:
    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "schedule", "set", "--all-repos", "--enabled", "on", "--frequency", "daily"],
    )

    assert result.exit_code == 0
    assert ports.targets.write_targets_calls == [WriteTargetsCall(None, "Pets", True, False, "mutation")]
    written = [
        (item.repo_id, item.audit_key, item.schedule.enabled, item.schedule.cadence)
        for item in ports.audit.schedule_writes
    ]
    assert written == [("r1", "audit.security", True, "daily"), ("r1", "audit.tests", True, "daily")]


def test_autofix_write_reaches_the_gateway_with_the_selected_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """The selector chooses which improvement job is rewritten, and only it."""
    audit = RecordingAuditGateway(catalog=AUTOFIX_CATALOG)
    installed = Ports(audit=audit)
    installed.install(monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "--project",
            "Pets",
            "improvement-jobs",
            "set",
            "test-writing",
            "--all-repos",
            "--enabled",
            "on",
            "--frequency",
            "weekly",
            "--timezone",
            "Asia/Almaty",
        ],
    )

    assert result.exit_code == 0
    assert installed.targets.write_targets_calls == [WriteTargetsCall(None, "Pets", True, False, "mutation")]
    assert [(item.repo_id, item.kind) for item in audit.autofix_writes] == [("r1", "test-writing")]
    written = audit.autofix_writes[0].job
    assert (written.action_key, written.variant_key) == ("improvement.test-writing", "default")
    assert (written.enabled, written.frequency, written.timezone) == (True, "weekly", "Asia/Almaty")


def test_autofix_write_keeps_the_existing_job_of_the_selected_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Picking the wrong existing job silently carries its settings forward.

    Each stored job here has a distinct schedule time, timezone and pentest
    mode, so a lookup that returns any job other than the requested kind
    rewrites the wrong values and fails this test.
    """
    stored = (
        AuditAutofixJob(
            "improvement.vuln-fix",
            "default",
            "vuln-fix",
            True,
            True,
            frequency="daily",
            days_of_week=("mon",),
            schedule_time="01:00",
            schedule_time_source="user",
            timezone="UTC",
            pentest_mode="on",
        ),
        AuditAutofixJob(
            "improvement.test-writing",
            "default",
            "test-writing",
            False,
            True,
            frequency="monthly",
            days_of_week=("fri",),
            schedule_time="23:00",
            schedule_time_source="user",
            timezone="Asia/Almaty",
            pentest_mode="off",
        ),
    )
    audit = RecordingAuditGateway(catalog=AUTOFIX_CATALOG, autofix_jobs={"r1": stored})
    Ports(audit=audit).install(monkeypatch)

    result = CliRunner().invoke(app, ["improvement-jobs", "set", "--repo", REPO, "test-writing", "--enabled", "on"])

    assert result.exit_code == 0
    assert [(item.repo_id, item.kind) for item in audit.autofix_writes] == [("r1", "test-writing")]
    written = audit.autofix_writes[0].job
    assert written.action_key == "improvement.test-writing"
    assert (written.schedule_time, written.days_of_week) == ("23:00", ("fri",))
    assert (written.timezone, written.pentest_mode, written.frequency) == ("Asia/Almaty", "off", "monthly")
    assert written.enabled is True


def test_batch_write_rejects_ambiguous_scope_before_application(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["email", "set", "--all-repos", "--all-projects", "--manual", "on"])

    assert result.exit_code == 1
    assert "pass --all-repos or --all-projects" in result.stderr
    assert ports.targets.write_targets_calls == []
    assert ports.audit.email_writes == []


def test_email_write_reaches_every_published_audit_of_the_repository(ports: Ports) -> None:
    result = CliRunner().invoke(app, ["email", "set", "--repo", REPO, "--manual", "on", "--scheduled", "off"])

    assert result.exit_code == 0
    assert [(item.repo_id, item.audit_key) for item in ports.audit.email_writes] == [
        ("r1", "audit.security"),
        ("r1", "audit.tests"),
    ]
    assert {(item.update.manual, item.update.scheduled) for item in ports.audit.email_writes} == {(True, False)}


def test_auth_import_bearer_requires_stdin_and_never_prints_credential(ports: Ports) -> None:
    missing = CliRunner().invoke(app, ["auth", "import-bearer"])
    assert missing.exit_code == 1
    assert "use --stdin" in missing.stderr
    assert ports.auth.imported_tokens == []

    result = CliRunner().invoke(app, ["auth", "import-bearer", "--stdin", "--json"], input="Bearer secret-token\n")
    assert result.exit_code == 0
    assert "secret-token" not in result.stdout
    assert ports.auth.imported_tokens == ["Bearer secret-token\n"]


def test_auth_failures_are_translated_into_the_application_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    """A credential-store failure must not reach delivery as an auth-library error."""
    auth = RecordingAuthSession(failure=AuthError("AUTH_CORRUPT", "credential file is unreadable"))
    Ports(auth=auth).install(monkeypatch)

    result = CliRunner().invoke(app, ["auth", "status"])

    assert result.exit_code == 3
    assert result.stderr.startswith("AUTH_CORRUPT: credential file is unreadable")
    assert auth.status_calls == 1


@pytest.mark.parametrize(
    ("error", "exit_code", "rendered"),
    [
        (EnjiApiError("BAD_SELECTOR", "unknown audit"), 4, "BAD_SELECTOR: unknown audit"),
        (ApplicationAuthError("AUTH_EXPIRED", "authentication expired"), 3, "AUTH_EXPIRED: authentication expired"),
        (OSError("disk full"), 1, "STORAGE: disk full"),
        (ValueError("invalid audit scope"), 1, "VALIDATION: invalid audit scope"),
    ],
)
def test_run_maps_current_application_errors_to_cli_contract(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    exit_code: int,
    rendered: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = ApplicationStubs().build()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: application)

    def fail() -> object:
        raise error

    with pytest.raises(typer.Exit) as caught:
        _run(fail, False, FIELDS_PRESENTATION)

    assert caught.value.exit_code == exit_code
    assert rendered in capsys.readouterr().err


def test_journey_telemetry_uses_application_command_exit_code() -> None:
    assert _command_exit_code(ApplicationCommandError("AUTH_EXPIRED", "expired", 3)) == 3
    assert _command_exit_code(ValueError("invalid")) == 1


def test_render_fields_preserves_semantic_shapes() -> None:
    assert render_fields({"status": "ready", "counts": {"ready": 2}, "items": ["a", "b"]}) == (
        'status: ready\ncounts: {"ready": 2}\nitems: ["a", "b"]'
    )
    assert render_fields([{"selector": "security", "enabled": True}, "pending"]) == (
        '{"enabled": true, "selector": "security"}\n"pending"'
    )
    assert render_fields("unchanged") == "unchanged"


def test_operation_text_renders_mapping_results_and_sequences() -> None:
    mapping = operation_text(
        {
            "status": "updated",
            "metadata": {"count": 2},
            "results": [
                {"audit_key": "audit.security", "status": "already_present"},
                {"action_key": "audit.tests", "status": "updated"},
                {"selector": "dependency-hygiene", "status": "unchanged"},
                "pending",
            ],
        }
    )
    assert "status: updated" in mapping
    assert 'metadata: {"count": 2}' in mapping
    assert "results:" in mapping
    assert "audit.security  status=already_present" in mapping
    assert "audit.tests  status=updated" in mapping
    assert "dependency-hygiene  status=unchanged" in mapping
    assert "  pending" in mapping

    sequence = operation_text([{"selector": "security", "status": "ready"}, "done"])
    assert sequence == "security  status=ready\ndone"
    assert operation_text(3) == "3"
