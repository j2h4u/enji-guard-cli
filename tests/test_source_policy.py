import ast
import json
import os
import re
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

RAW_GATEWAY_MODULES = frozenset(
    {
        "enji_guard_cli.enji_gateway.wire",
        "enji_guard_cli.enji_gateway.http",
        "enji_guard_cli.enji_gateway.contract",
        "enji_guard_cli.enji_gateway.client",
        "enji_guard_cli.transport",
    }
)
PRODUCT_SOURCE_ROOTS = (
    ROOT / "src" / "enji_guard_cli" / "audit",
    ROOT / "src" / "enji_guard_cli" / "portfolio",
    ROOT / "src" / "enji_guard_cli" / "application.py",
    ROOT / "src" / "enji_guard_cli" / "delivery",
)
SETUP_UV_ACTION = "astral-sh/setup-uv@"
TRIVY_ACTION = "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25"
UV_VERSION = "0.11.17"


def _compose_common_service_fields(path: Path) -> dict[str, object]:
    compose = _compose_config(path)
    services = cast(dict[str, object], compose["services"])
    service = cast(dict[str, object], services["enji-guard-cli"])
    return {field: service[field] for field in COMMON_SERVICE_FIELDS}


def _compose_config(path: Path) -> dict[str, object]:
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
    return cast(dict[str, object], json.loads(result.stdout))


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


def test_ghcr_compose_declares_stable_project_name() -> None:
    compose = _compose_config(ROOT / "deploy" / "docker-compose.ghcr.yml")

    assert compose["name"] == "enji-guard-cli"


def test_container_publish_workflow_run_requires_trusted_source() -> None:
    workflow = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")

    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow


def test_setup_uv_installs_the_dockerfile_version() -> None:
    workflows = tuple(sorted((ROOT / ".github" / "workflows").glob("*.yml")))
    setup_uv_steps = [
        step for workflow in workflows for step in _action_steps(workflow.read_text(encoding="utf-8"), SETUP_UV_ACTION)
    ]

    assert setup_uv_steps
    assert all(f'version: "{UV_VERSION}"' in step for step in setup_uv_steps)
    assert f"ghcr.io/astral-sh/uv:{UV_VERSION}@sha256:" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_container_publish_scans_loaded_candidate_before_push() -> None:
    workflow = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")
    build = workflow.index("- name: Build candidate image")
    scan = workflow.index("- name: Scan candidate image")
    publish = workflow.index("- name: Publish tested image")
    scan_step = _action_steps(workflow, TRIVY_ACTION)
    candidate_build = workflow[build:scan]

    assert build < scan < publish
    assert "load: true" in candidate_build
    assert "tags: ${{ steps.image-tags.outputs.tags }}" in candidate_build
    assert len(scan_step) == 1
    assert "scan-type: image" in scan_step[0]
    assert "image-ref: ${{ env.IMAGE_NAME }}:latest" in scan_step[0]
    assert 'exit-code: "1"' in scan_step[0]
    assert "ignore-unfixed: true" in scan_step[0]


def test_audit_schedule_domain_has_no_improvement_job_fallback() -> None:
    schedules = (ROOT / "src" / "enji_guard_cli" / "audit" / "schedules.py").read_text(encoding="utf-8")

    assert "audit_auto_run_key" in schedules
    assert "improvement" not in schedules
    assert "runbook" not in schedules


def test_product_source_does_not_import_raw_gateway_implementations() -> None:
    violations: list[str] = []
    for path in _product_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for imported in _imported_modules(node):
                if any(imported == module or imported.startswith(f"{module}.") for module in RAW_GATEWAY_MODULES):
                    violations.append(f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}: {imported}")
    assert violations == [], "raw gateway imports leaked into product code:\n" + "\n".join(violations)


def _product_python_files() -> tuple[Path, ...]:
    paths: list[Path] = []
    for root in PRODUCT_SOURCE_ROOTS:
        if root.is_file():
            paths.append(root)
        else:
            paths.extend(root.rglob("*.py"))
    return tuple(sorted(paths))


def _action_steps(workflow: str, action: str) -> tuple[str, ...]:
    pattern = re.compile(rf"(?m)^ +(?:- +)?uses: {re.escape(action)}[^\n]*\n(?:^ {{8,}}\S[^\n]*\n?)*")
    return tuple(match.group(0) for match in pattern.finditer(workflow))


def _imported_modules(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        if node.module is None:
            return ()
        if node.module == "enji_guard_cli.enji_gateway":
            return tuple(f"{node.module}.{alias.name}" for alias in node.names)
        return (node.module,)
    return ()
