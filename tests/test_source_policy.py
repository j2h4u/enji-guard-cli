import ast
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
