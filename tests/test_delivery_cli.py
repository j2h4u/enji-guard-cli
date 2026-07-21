import importlib
import json
from collections.abc import Callable, Mapping
from typing import cast

import pytest
import typer
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from enji_guard_cli.application import (
    Application,
    ApplicationAuthError,
    ApplicationCommandError,
    ApplicationResult,
    AutofixListing,
    AutofixListingItem,
    AutofixWriteScope,
    ScheduleListing,
)
from enji_guard_cli.audit.artifacts import AuditSummary, AuditSummaryItem
from enji_guard_cli.audit.ports import (
    AuditAutofixDefinition,
    AuditAutofixJob,
    AuditFreshness,
    AuditGatewayPort,
    AuditSchedule,
    AuditStatus,
    AuditStatusItem,
)
from enji_guard_cli.auth_session.service import AuthSessionService
from enji_guard_cli.delivery.cli.app import _command_exit_code, _json, _run, app
from enji_guard_cli.errors import EnjiApiError
from enji_guard_cli.portfolio.models import ProjectRef, RepositoryIdentity, RepositoryProvider, RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioAuditStatus, PortfolioGatewayPort
from enji_guard_cli.portfolio.status import PortfolioOverview, ProjectOverview, RepositoryOverview, RepositoryStatus


def _repo(locator: str, *, scores: Mapping[str, float | int | None] | None = None) -> RepositoryRef:
    return RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, locator, "github.com"),
        scores=scores or {},
        web_url="https://example.test/repository",
        provider_repo_id="provider-test",
    )


cli_module = importlib.import_module("enji_guard_cli.delivery.cli.app")


def test_operator_command_tree_uses_audit_vocabulary() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("auth", "project", "repo", "recon", "audit", "schedule", "improvement-jobs", "email", "language"):
        assert command in result.stdout


def test_audit_read_and_summary_are_public_commands() -> None:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    audit = root.commands["audit"]
    assert isinstance(audit, TyperGroup)
    assert set(audit.commands) >= {"read", "summary", "start", "wait"}
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
    assert captured["transport"] == "streamable-http"
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 18080


def test_run_keeps_stdio_as_an_explicit_interactive_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_service(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "run_service", fake_run_service)

    result = CliRunner().invoke(app, ["run", "--transport", "stdio"])

    assert result.exit_code == 0
    assert captured["transport"] == "stdio"


class _FakeAuth:
    def status(self) -> dict[str, object]:
        return {"authenticated": True, "credential_type": "bearer"}

    def refresh(self) -> dict[str, object]:
        return {"ok": True, "credential_type": "cookie"}

    def import_cookie(self, value: str) -> dict[str, object]:
        return {"ok": True, "credential_type": "cookie", "length": len(value)}

    def import_bearer_token(self, value: str) -> dict[str, object]:
        return {"ok": True, "credential_type": "bearer", "length": len(value)}


class _FakeApplication:
    def __init__(self) -> None:
        self.auth = _FakeAuth()
        self.calls: list[tuple[str, object]] = []

    def execute(self, action: Callable[[], object]) -> ApplicationResult:
        return ApplicationResult(action())

    def project_settings(self, project: str | None) -> object:
        self.calls.append(("project_settings", project))
        return {"project": project, "repositories": []}

    def import_bearer(self, value: str) -> dict[str, object]:
        return self.auth.import_bearer_token(value)

    def access(self) -> object:
        self.calls.append(("access", None))
        return {"full_access": True}

    def portfolio_overview(self, project: str | None, sort: str) -> object:
        self.calls.append(("portfolio_overview", (project, sort)))
        return {"observed_at": "2026-07-20T00:00:00Z", "projects": []}

    def repository_status(self, repo: str, project: str | None) -> object:
        self.calls.append(("repository_status", (repo, project)))
        return {
            "repository": {
                "identity": {"provider": "github", "host": "github.com", "locator": repo},
                "web_url": f"https://github.com/{repo}",
                "provider_repo_id": "provider-1",
            }
        }

    def audit_summary(self, repo: str, selectors: list[str], *, project: str | None) -> object:
        self.calls.append(("audit_summary", (repo, selectors, project)))
        return AuditSummary(repo, ())

    def audit_start(self, repo: str, project: str | None, selectors: list[str], *, all_audits: bool) -> object:
        self.calls.append(("audit_start", (repo, project, selectors, all_audits)))
        return {"repo_id": repo, "project_id": project, "results": [{"state": "started"}]}

    def set_schedules(self, repo: str | None, project: str | None, **options: object) -> object:
        self.calls.append(("set_schedules", (repo, project, options)))
        return [{"state": "unchanged"}]

    def list_schedules(self, repo: str | None, project: str | None) -> object:
        self.calls.append(("list_schedules", (repo, project)))
        return ()

    def set_email_preferences(self, repo: str | None, project: str | None, update: object, *, scope: object) -> object:
        self.calls.append(("set_email_preferences", (repo, project, update, scope)))
        return [{"state": "changed"}]

    def set_autofixes(self, *args: object, **options: object) -> object:
        self.calls.append(("set_autofixes", (*args, options)))
        return [{"state": "unchanged"}]

    def list_autofixes(self, repo: str | None, project: str | None) -> object:
        self.calls.append(("list_autofixes", (repo, project)))
        return ()


def test_audit_start_calls_typed_application_and_emits_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    result = CliRunner().invoke(app, ["audit", "start", "org/repo", "security", "--project", "Pets", "--json"])
    assert result.exit_code == 0
    assert result.exception is None
    payload = cast(dict[str, object], json.loads(result.stdout))
    assert payload["repo_id"] == "org/repo"
    assert payload["project_id"] == "Pets"
    assert fake.calls == [("audit_start", ("org/repo", "Pets", ["security"], False))]


def test_project_settings_and_access_use_typed_application_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    settings = CliRunner().invoke(app, ["project", "settings", "--project", "Pets", "--json"])
    access = CliRunner().invoke(app, ["access", "--json"])
    assert settings.exit_code == 0
    assert access.exit_code == 0
    assert fake.calls[:2] == [("project_settings", "Pets"), ("access", None)]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--project", "Pets", "status", "--sort", "weakest", "--json"],
        ["--project", "Pets", "portfolio", "status", "--sort", "weakest", "--json"],
        ["--project", "Pets", "repo", "list", "--sort", "weakest", "--json"],
    ],
)
def test_portfolio_commands_use_compact_overview(monkeypatch: pytest.MonkeyPatch, arguments: list[str]) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 0
    assert fake.calls == [("portfolio_overview", ("Pets", "weakest"))]


def test_status_for_one_repository_keeps_detailed_status(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

    result = CliRunner().invoke(app, ["--project", "Pets", "status", "github@github.com:acme/cat", "--json"])

    assert result.exit_code == 0
    assert fake.calls == [("repository_status", ("github@github.com:acme/cat", "Pets"))]


def test_portfolio_text_is_compact_and_scenario_oriented(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    overview = PortfolioOverview(
        "2026-07-20T00:00:00Z",
        (
            ProjectOverview(
                ProjectRef("p1", "Pets"),
                (
                    RepositoryOverview(
                        RepositoryRef(
                            "r1",
                            "p1",
                            "Pets",
                            RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
                            recon_done=True,
                            scores={"tests": 80, "vulns": 40},
                            web_url="https://example.test/repository",
                            provider_repo_id="provider-test",
                        )
                    ),
                ),
            ),
        ),
    )
    monkeypatch.setattr(fake, "portfolio_overview", lambda project, sort: overview)
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Pets" in result.stdout
    assert "github@github.com:acme/cat  weakest=40 overall=60.0 recon=ready active=0" in result.stdout
    assert '"projects"' not in result.stdout


def test_repository_status_text_is_compact_and_does_not_dump_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    freshness = AuditFreshness("head", "head", "fresh")
    item = AuditStatusItem("audit.security", "Security", freshness, True, "completed", "t1", "completed")
    payload = (
        RepositoryStatus(
            _repo("acme/cat"),
            PortfolioAuditStatus(AuditStatus("r1", "head", (item,))),
        ),
    )
    monkeypatch.setattr(fake, "repository_status", lambda repo, project: payload)
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

    result = CliRunner().invoke(app, ["status", "github@github.com:acme/cat"])

    assert result.exit_code == 0
    assert "audits: total=1 ready=1 active=0 stale=0 failed=0" in result.stdout
    assert "security  state=ready freshness=fresh" in result.stdout
    assert '"audit_key"' not in result.stdout


def test_audit_summary_is_compact_in_text_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    payload = AuditSummary(
        "r1",
        (
            AuditSummaryItem(
                "audit.security", True, 73, "2026-07-20T00:00:00Z", None, AuditFreshness("h", "h", "fresh")
            ),
        ),
    )
    monkeypatch.setattr(fake, "audit_summary", lambda repo, selectors, project=None: payload)
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

    text_result = CliRunner().invoke(app, ["audit", "summary", "github@github.com:acme/cat"])
    json_result = CliRunner().invoke(app, ["audit", "summary", "github@github.com:acme/cat", "--json"])

    assert text_result.exit_code == 0
    assert "security  score=73 freshness=fresh" in text_result.stdout
    assert "body" not in text_result.stdout
    assert json_result.exit_code == 0
    payload = cast(dict[str, object], json.loads(json_result.stdout))
    audits = cast(list[dict[str, object]], payload["audits"])
    assert payload["repo_id"] == "r1"
    assert audits[0]["score"] == 73
    assert "body" not in audits[0]


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
    fake = _FakeApplication()
    repository = _repo("acme/cat")
    payload = (
        ScheduleListing(
            repository,
            (
                AuditSchedule("audit.security", True, "workdays", None, None, "09:00", "auto", "Asia/Almaty"),
                AuditSchedule("audit.tests", False, "weekly", None, None, "10:00", "user", "UTC"),
            ),
        ),
    )
    monkeypatch.setattr(fake, "list_schedules", lambda repo, project: payload)
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

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
    repository = cast(dict[str, object], payload[0]["repository"])
    schedules = cast(list[object], payload[0]["schedules"])
    identity = cast(dict[str, object], repository["identity"])
    assert identity["locator"] == "acme/cat"
    assert len(schedules) == 2


def test_json_preserves_null_scores_from_typed_repository_dto() -> None:
    rendered = _json(_repo("acme/cat", scores={"audit.security": None, "audit.tests": 0}))

    assert isinstance(rendered, dict)
    assert rendered["scores"] == {"audit.security": None, "audit.tests": 0}


def test_improvement_jobs_list_is_one_summary_line_per_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    repository = _repo("acme/cat")
    definition = AuditAutofixDefinition(
        "improvement.test-writing", "default", "Tests", None, "audit.tests", "test-writing", True
    )
    job = AuditAutofixJob(
        "improvement.test-writing", "default", "test-writing", True, True, frequency="workdays", timezone="UTC"
    )
    payload = (AutofixListing(repository, (AutofixListingItem(definition, job),)),)
    monkeypatch.setattr(fake, "list_autofixes", lambda repo, project: payload)
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

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


def test_improvement_jobs_text_preserves_mixed_dimensions_and_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeApplication()
    repository = _repo("acme/cat")
    definitions = tuple(
        AuditAutofixDefinition(f"improvement.{selector}", "default", selector, None, None, selector, True)
        for selector in ("security-fix", "dependency-update", "test-writing")
    )
    jobs = (
        AuditAutofixJob(
            "improvement.security-fix",
            "default",
            "security-fix",
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
        None,
    )
    payload = (
        AutofixListing(
            repository,
            tuple(AutofixListingItem(definition, job) for definition, job in zip(definitions, jobs, strict=True)),
        ),
    )
    monkeypatch.setattr(fake, "list_autofixes", lambda repo, project: payload)
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

    result = CliRunner().invoke(app, ["improvement-jobs", "list"])

    assert result.exit_code == 0
    assert "enabled=1/3 configured=2/3 auto_fix=0/3" in result.stdout
    assert "enabled_state=mixed[security-fix=true,dependency-update=false]" in result.stdout
    assert "auto_fix_state=mixed[security-fix=false,dependency-update=unset]" in result.stdout
    assert "frequency=mixed[security-fix=daily,dependency-update=weekly]" in result.stdout
    assert "days=mixed[security-fix=mon,dependency-update=fri]" in result.stdout
    assert "schedule_time=mixed[security-fix=09:00,dependency-update=10:00]" in result.stdout
    assert "schedule_time_source=mixed[security-fix=auto,dependency-update=user]" in result.stdout
    assert "pentest_mode=mixed[security-fix=off,dependency-update=on]" in result.stdout
    assert "unconfigured=test-writing disabled=dependency-update" in result.stdout


def test_improvement_jobs_text_does_not_report_unknown_enabled_state_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeApplication()
    repository = _repo("acme/cat")
    definition = AuditAutofixDefinition(
        "improvement.test-writing", "default", "Tests", None, "audit.tests", "test-writing", True
    )
    job = AuditAutofixJob("improvement.test-writing", "default", "test-writing", None, True)
    payload = (AutofixListing(repository, (AutofixListingItem(definition, job),)),)
    monkeypatch.setattr(fake, "list_autofixes", lambda repo, project: payload)
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

    result = CliRunner().invoke(app, ["improvement-jobs", "list"])

    assert result.exit_code == 0
    assert "enabled=0/1" in result.stdout
    assert "disabled=" not in result.stdout
    assert "enabled_unknown=test-writing" in result.stdout


def test_schedule_list_groups_restricted_window_days_by_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    repository = _repo("acme/cat")
    payload = (
        ScheduleListing(
            repository,
            (
                AuditSchedule("audit.security", True, "daily", None, None, None, "auto", "UTC", ("mon", "wed")),
                AuditSchedule("audit.tests", True, "daily", None, None, None, "auto", "UTC", ("mon", "wed")),
                AuditSchedule("audit.deps", True, "daily", None, None, None, "auto", "UTC", ("fri",)),
            ),
        ),
    )
    monkeypatch.setattr(fake, "list_schedules", lambda repo, project: payload)
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

    result = CliRunner().invoke(app, ["schedule", "list"])

    assert result.exit_code == 0
    assert "window_days=mon,wed:security,tests|fri:deps" in result.stdout


def test_batch_write_options_are_forwarded_with_explicit_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    result = CliRunner().invoke(
        app,
        ["--project", "Pets", "schedule", "set", "--all-repos", "--enabled", "on", "--frequency", "daily"],
    )
    assert result.exit_code == 0
    name, args = fake.calls[-1]
    assert name == "set_schedules"
    values = cast(tuple[object, object, dict[str, object]], args)
    assert values[0:2] == (None, "Pets")
    assert cast(AutofixWriteScope, values[2]["scope"]).all_repos is True


def test_autofix_write_options_are_forwarded_with_canonical_keyword_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)

    result = CliRunner().invoke(
        app,
        [
            "--project",
            "Pets",
            "improvement-jobs",
            "set",
            "tests/test-writing",
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
    name, args = fake.calls[-1]
    assert name == "set_autofixes"
    values = cast(tuple[object, object, object, dict[str, object]], args)
    assert values[:3] == (None, "Pets", ["tests/test-writing"])
    assert values[3]["enabled"] is True
    assert values[3]["cadence"] == "weekly"
    assert values[3]["timezone"] == "Asia/Almaty"
    assert cast(AutofixWriteScope, values[3]["scope"]).all_repos is True


def test_batch_write_rejects_ambiguous_scope_before_application(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    result = CliRunner().invoke(app, ["email", "set", "--all-repos", "--all-projects", "--manual", "on"])
    assert result.exit_code == 1
    assert "pass --all-repos or --all-projects" in result.stderr
    assert fake.calls == []


def test_auth_import_bearer_requires_stdin_and_never_prints_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeApplication()
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: fake)
    missing = CliRunner().invoke(app, ["auth", "import-bearer"])
    assert missing.exit_code == 1
    assert "use --stdin" in missing.stderr
    result = CliRunner().invoke(app, ["auth", "import-bearer", "--stdin", "--json"], input="Bearer secret-token\n")
    assert result.exit_code == 0
    assert "secret-token" not in result.stdout


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
    application = Application(
        audit_gateway=cast(AuditGatewayPort, object()),
        portfolio_gateway=cast(PortfolioGatewayPort, object()),
        auth=cast(AuthSessionService, object()),
    )
    monkeypatch.setattr(cli_module, "_application", lambda auth_file=None: application)

    def fail() -> object:
        raise error

    with pytest.raises(typer.Exit) as caught:
        _run(fail, False)

    assert caught.value.exit_code == exit_code
    assert rendered in capsys.readouterr().err


def test_journey_telemetry_uses_application_command_exit_code() -> None:
    assert _command_exit_code(ApplicationCommandError("AUTH_EXPIRED", "expired", 3)) == 3
    assert _command_exit_code(ValueError("invalid")) == 1
