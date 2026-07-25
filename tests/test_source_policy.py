import ast
import json
import os
import re
import subprocess
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_IMAGE_REF = "ghcr.io/j2h4u/enji-guard-cli@sha256:" + "0" * 64
COMPOSE_PACKAGE_VERSION = "1.2.3+local.test"
COMPOSE_SOURCE_COMMIT = "13920645c8c7"
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
    ROOT / "src" / "enji_guard_cli" / "application",
    ROOT / "src" / "enji_guard_cli" / "delivery",
    ROOT / "src" / "enji_guard_cli" / "mcp_facade.py",
)
BUILD_PUSH_ACTION = "docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a"
SETUP_UV_ACTION = "astral-sh/setup-uv@"
TRIVY_ACTION = "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25"
UV_VERSION = "0.11.17"


def _compose_common_service_fields(path: Path) -> dict[str, object]:
    compose = _compose_config(path)
    services = cast(dict[str, object], compose["services"])
    service = cast(dict[str, object], services["enji-guard-cli"])
    return {field: service[field] for field in COMMON_SERVICE_FIELDS}


def _compose_config(path: Path, *, host_port: str | None = None) -> dict[str, object]:
    environment = os.environ.copy()
    environment["ENJI_GUARD_IMAGE_REF"] = COMPOSE_IMAGE_REF
    environment["PACKAGE_VERSION"] = COMPOSE_PACKAGE_VERSION
    environment["SOURCE_COMMIT"] = COMPOSE_SOURCE_COMMIT
    if host_port is not None:
        environment["ENJI_GUARD_MCP_HOST_PORT"] = host_port
    result = subprocess.run(
        ["docker", "compose", "-f", str(path.relative_to(ROOT)), "config", "--format", "json"],
        check=True,
        capture_output=True,
        cwd=ROOT,
        env=environment,
        text=True,
    )
    return cast(dict[str, object], json.loads(result.stdout))


@pytest.mark.docker
def test_local_compose_requires_build_provenance() -> None:
    environment = os.environ.copy()
    environment.pop("PACKAGE_VERSION", None)
    environment.pop("SOURCE_COMMIT", None)

    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        capture_output=True,
        cwd=ROOT,
        env=environment,
        text=True,
    )

    assert result.returncode != 0
    assert "required variable" in result.stderr
    assert any(variable in result.stderr for variable in ("PACKAGE_VERSION", "SOURCE_COMMIT"))

    build = subprocess.run(
        ["docker", "compose", "build"],
        capture_output=True,
        cwd=ROOT,
        env=environment,
        text=True,
    )
    assert build.returncode != 0
    assert "required variable" in build.stderr
    assert any(variable in build.stderr for variable in ("PACKAGE_VERSION", "SOURCE_COMMIT"))


@pytest.mark.docker
def test_local_compose_passes_non_placeholder_build_provenance() -> None:
    compose = _compose_config(ROOT / "docker-compose.yml")
    services = cast(dict[str, object], compose["services"])
    service = cast(dict[str, object], services["enji-guard-cli"])
    build = cast(dict[str, object], service["build"])
    args = cast(dict[str, str], build["args"])

    assert args == {"PACKAGE_VERSION": COMPOSE_PACKAGE_VERSION, "SOURCE_COMMIT": COMPOSE_SOURCE_COMMIT}


def test_dockerfile_default_command_is_loopback_safe() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'CMD ["run"]' in dockerfile
    assert '"--allow-external-host"' not in dockerfile


def test_dockerfile_runtime_dependency_layer_disables_source_builds() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "uv sync --frozen --no-build --no-install-project --no-dev" in dockerfile


def test_dockerfile_rejects_placeholder_build_provenance() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG PACKAGE_VERSION=0.0.0+local" not in dockerfile
    assert "ARG SOURCE_COMMIT=unknown" not in dockerfile
    assert 'RUN PACKAGE_VERSION="${PACKAGE_VERSION}" SOURCE_COMMIT="${SOURCE_COMMIT}" python' in dockerfile
    assert "PACKAGE_VERSION must be a non-0.0.0 semantic version" in dockerfile
    assert "SOURCE_COMMIT must be a Git object id" in dockerfile


@pytest.mark.docker
def test_local_and_ghcr_compose_critical_settings_stay_in_sync() -> None:
    local = _compose_common_service_fields(ROOT / "docker-compose.yml")
    ghcr = _compose_common_service_fields(ROOT / "deploy" / "docker-compose.ghcr.yml")

    assert local == ghcr


@pytest.mark.docker
def test_compose_publishes_mcp_on_configurable_nonconflicting_host_port() -> None:
    for path in (ROOT / "docker-compose.yml", ROOT / "deploy" / "docker-compose.ghcr.yml"):
        compose = _compose_config(path)
        services = cast(dict[str, object], compose["services"])
        service = cast(dict[str, object], services["enji-guard-cli"])
        ports = cast(list[dict[str, object]], service["ports"])

        assert ports == [
            {"host_ip": "127.0.0.1", "mode": "ingress", "protocol": "tcp", "published": "18082", "target": 8000}
        ]

        overridden = _compose_config(path, host_port="18081")
        overridden_services = cast(dict[str, object], overridden["services"])
        overridden_service = cast(dict[str, object], overridden_services["enji-guard-cli"])
        overridden_ports = cast(list[dict[str, object]], overridden_service["ports"])

        assert overridden_ports == [
            {"host_ip": "127.0.0.1", "mode": "ingress", "protocol": "tcp", "published": "18081", "target": 8000}
        ]


@pytest.mark.docker
def test_ghcr_compose_declares_stable_project_name() -> None:
    compose = _compose_config(ROOT / "deploy" / "docker-compose.ghcr.yml")

    assert compose["name"] == "enji-guard-cli"


def test_container_publish_has_exactly_one_entry_point() -> None:
    # Two publishing runs for one commit build two different digests and race
    # for :latest and for the tag the attestation subject is resolved from, so
    # the container workflow must stay reachable only through release.yml (plus
    # the manual workflow_dispatch escape hatch).
    container = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")

    assert _workflow_triggers(container) == ("workflow_call", "workflow_dispatch")
    assert "workflow_run" not in container

    callers = [
        path
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        if "uses: ./.github/workflows/container.yml" in path.read_text(encoding="utf-8")
    ]

    assert [path.name for path in callers] == ["release.yml"]


def test_release_publishes_only_after_ci_succeeds_on_the_commit() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert _workflow_triggers(release) == ("push", "workflow_dispatch")
    assert "actions/workflows/ci.yml/runs?head_sha=" in release
    assert re.search(r"^\s*needs: \[release-please, wait-for-ci\]$", release, re.MULTILINE)


def test_docker_marked_policy_tests_run_in_ci() -> None:
    # pyproject's addopts deselect the `docker` marker, so `just unit` skips
    # these.  docker-build is the only CI job with a daemon; if it stops
    # invoking them the packaging policy silently becomes local-only.
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    marked = [item for item in _source_files_marked_docker() if item]

    assert marked
    assert "-m docker" in (ROOT / "Justfile").read_text(encoding="utf-8")
    assert "run: just docker-tests" in ci


def test_publishing_workflows_are_not_cancellable() -> None:
    # A cancellation between the first registry push and the attestation steps
    # leaves promoted tags live with no provenance and no rollback.
    for name in ("container.yml", "release.yml"):
        workflow = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

        assert "cancel-in-progress: false" in workflow, name
        assert "cancel-in-progress: true" not in workflow, name


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
    build_steps = _action_steps(workflow, BUILD_PUSH_ACTION)
    candidate_build = workflow[build:scan]

    assert build < scan < publish
    assert "load: true" in candidate_build
    assert "push: false" in candidate_build
    assert "tags: ${{ steps.image-tags.outputs.tags }}" in candidate_build
    assert "docker push" not in workflow[:scan]
    assert len(scan_step) == 1
    assert "scan-type: image" in scan_step[0]
    assert "image-ref: ${{ env.IMAGE_NAME }}:sha-${{ steps.release-metadata.outputs.source-sha }}" in scan_step[0]
    assert 'exit-code: "1"' in scan_step[0]
    assert "ignore-unfixed: true" in scan_step[0]

    # Ordering alone would not notice a second builder added after the scan, so
    # pin that the workflow builds exactly once and that no build anywhere in it
    # is allowed to push.  Every byte that reaches GHCR must come from the one
    # image Trivy looked at.
    assert len(build_steps) == 1
    assert "push: true" not in workflow
    assert all("push: false" in step and "load: true" in step for step in build_steps)


def test_container_publish_attests_before_promoting_mutable_tags() -> None:
    workflow = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")
    publish = workflow.index("- name: Publish tested image")
    provenance = workflow.index("- name: Attest build provenance")
    sbom = workflow.index("- name: Attest SBOM")
    promote = workflow.index("- name: Promote attested tags")

    # The publish step may push the immutable sha tag only.  :latest and the
    # version tags are promoted after the attestations exist, so a failure
    # between the two leaves the tags colleagues actually pull untouched.
    assert publish < provenance < sbom < promote
    assert workflow.count("docker push") == 1
    assert 'docker push "${candidate}"' in workflow[publish:provenance]
    assert "docker buildx imagetools create" not in workflow[:sbom]

    # The attested subject must be provably the scanned image, not whatever the
    # registry happens to serve under the tag by the time it is read back.
    assert ".RepoDigests" in workflow[publish:provenance]
    for subject in (workflow[provenance:sbom], workflow[sbom:promote]):
        assert "subject-digest: ${{ steps.publish.outputs.digest }}" in subject


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
        assert root.exists(), f"product source root no longer exists: {root}"
        if root.is_file():
            paths.append(root)
        else:
            paths.extend(root.rglob("*.py"))
    return tuple(sorted(paths))


def _action_steps(workflow: str, action: str) -> tuple[str, ...]:
    pattern = re.compile(rf"(?m)^ +(?:- +)?uses: {re.escape(action)}[^\n]*\n(?:^ {{8,}}\S[^\n]*\n?)*")
    return tuple(match.group(0) for match in pattern.finditer(workflow))


def _source_files_marked_docker() -> tuple[str, ...]:
    """Return the test files that carry at least one `docker` marker."""
    return tuple(
        path.name
        for path in sorted((ROOT / "tests").glob("test_*.py"))
        if "@pytest.mark.docker" in path.read_text(encoding="utf-8")
    )


def _workflow_triggers(workflow: str) -> tuple[str, ...]:
    """Return the event names declared under the workflow's top-level `on:` key."""
    block = re.search(r"(?m)^on:\n((?:(?:[ \t#].*)?\n)*)", workflow)
    if block is None:
        raise AssertionError("workflow has no top-level 'on:' block")
    return tuple(match.group(1) for match in re.finditer(r"(?m)^ {2}([a-z_]+):", block.group(1)))


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
