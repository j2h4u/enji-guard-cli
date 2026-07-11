from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_default_command_is_loopback_safe() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'CMD ["run"]' in dockerfile
    assert '"--allow-external-host"' not in dockerfile


def test_dockerfile_runtime_dependency_layer_disables_source_builds() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "uv sync --frozen --no-build --no-install-project --no-dev" in dockerfile


def test_container_publish_workflow_run_requires_trusted_source() -> None:
    workflow = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")

    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow


def test_audit_schedule_domain_has_no_improvement_job_fallback() -> None:
    schedules = (ROOT / "src" / "enji_guard_cli" / "core_impl" / "schedules.py").read_text(encoding="utf-8")
    write_settings = (ROOT / "src" / "enji_guard_cli" / "core_impl" / "write_settings.py").read_text(encoding="utf-8")
    core = (ROOT / "src" / "enji_guard_cli" / "core.py").read_text(encoding="utf-8")

    assert "subscriptions" in schedules
    assert "schedule_subscription_by_action_key" in write_settings
    assert "audit_auto_runs" in core
    for source in (schedules, write_settings):
        assert "improvement" not in source
        assert "runbook" not in source
        assert "autofix" not in source.lower()
