import json
import os
import subprocess
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_IMAGE_REF = "ghcr.io/j2h4u/enji-guard-cli@sha256:" + "0" * 64
COMMON_SERVICE_FIELDS = (
    "command",
    "restart",
    "init",
    "ports",
    "mem_limit",
    "memswap_limit",
    "pids_limit",
    "read_only",
    "cap_drop",
    "security_opt",
    "tmpfs",
    "healthcheck",
    "volumes",
)


def _compose_common_service_fields(path: Path) -> dict[str, object]:
    environment = os.environ.copy()
    environment["ENJI_GUARD_IMAGE_REF"] = COMPOSE_IMAGE_REF
    result = subprocess.run(
        ["docker", "compose", "-f", str(path.relative_to(ROOT)), "config", "--format", "json"],
        check=True,
        capture_output=True,
        cwd=ROOT,
        env=environment,
        text=True,
    )
    compose = cast(dict[str, object], json.loads(result.stdout))
    services = cast(dict[str, object], compose["services"])
    service = cast(dict[str, object], services["enji-guard-cli"])
    return {field: service[field] for field in COMMON_SERVICE_FIELDS}


def test_dockerfile_default_command_is_loopback_safe() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'CMD ["run"]' in dockerfile
    assert '"--allow-external-host"' not in dockerfile


def test_dockerfile_runtime_dependency_layer_disables_source_builds() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "uv sync --frozen --no-build --no-install-project --no-dev" in dockerfile


def test_local_and_ghcr_compose_critical_settings_stay_in_sync() -> None:
    local = _compose_common_service_fields(ROOT / "docker-compose.yml")
    ghcr = _compose_common_service_fields(ROOT / "deploy" / "docker-compose.ghcr.yml")

    assert local == ghcr


def test_container_publish_workflow_run_requires_trusted_source() -> None:
    workflow = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")

    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow


def test_audit_schedule_domain_has_no_improvement_job_fallback() -> None:
    schedules = (ROOT / "src" / "enji_guard_cli" / "audit" / "schedules.py").read_text(encoding="utf-8")

    assert "audit_auto_run_key" in schedules
    assert "improvement" not in schedules
    assert "runbook" not in schedules


def test_legacy_facades_are_deleted() -> None:
    package = ROOT / "src" / "enji_guard_cli"

    assert not (package / "core.py").exists()
    assert not (package / "core_impl").exists()
    assert not (package / "cli.py").exists()
    assert not (package / "cli_impl").exists()
