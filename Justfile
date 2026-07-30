set shell := ["bash", "-euo", "pipefail", "-c"]
export UV_LINK_MODE := "hardlink"

# Show available repo commands.
default:
    @just --list

# Compile Python sources for syntax errors.
compile:
    uv run python -m compileall -q src scripts tests

# Lint with ruff across the whole repo.
lint:
    uv run ruff check --preview src scripts tests

# Check preview complexity/refactor rules not covered by prefix selection.
preview-complexity-lint:
    uv run ruff check --preview --select PLR0914,PLR0916,PLR0917 src scripts tests

# Guard production code from raw print/debug output.
print-lint:
    uv run ruff check --preview --select T20 src/enji_guard_cli

# Check formatting without writing.
fmt-check:
    uv run ruff format --no-preview --check src scripts tests

# Install the git pre-commit hooks (see .pre-commit-config.yaml).
# Install both stages: pre-commit judges the files, commit-msg judges the
# message.  Without --hook-type commit-msg the message check never runs.
hooks:
    uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

# Module boundaries, enforced by tach.  This is the architecture gate: tach.toml
# declares the whole module graph, so an undeclared edge fails rather than
# passing unnoticed, which is what import-linter's contract-by-contract model
# could not do.
#
# Three checks, because tach's own `exact = true` only covers the first:
#   - `check`          : no undeclared or unused dependency edge;
#   - `check-external` : every imported third-party module is a declared dependency;
#   - interface gate   : no `expose` pattern that matches nothing, which would
#                        keep a wider public surface open than the code uses.
module-boundaries:
    uv run tach check
    # Tach 0.35 does not read PEP 621 optional extras. Exclude only the MCP
    # adapter that is guarded by the `mcp` extra; deptry and package-check still
    # validate its declaration and both base/extra install modes.
    uv run tach check-external -e src/enji_guard_cli/delivery/mcp/server.py
    scripts/check_tach_interfaces.py

# Validate GitHub Actions workflow syntax and expressions.
actionlint:
    uv run actionlint

# Validate the reconstructed Enji OpenAPI contract.
openapi-semantic-contract:
    scripts/validate_openapi_contract.py

# Validate OpenAPI schema shape with the upstream validator.
openapi-schema-contract:
    uv run openapi-spec-validator contracts/enji-openapi.json

# Validate the reconstructed Enji OpenAPI contract.
openapi-contract: openapi-semantic-contract openapi-schema-contract

# Run the production-only basedpyright ratchet.  The locked baseline represents
# the current unknown-type debt; every new or moved production unknown fails.
typecheck:
    uv run basedpyright --project basedpyright.production.json --baselinemode=lock

# Scripts keep the repository's existing standard-mode checks without being
# folded into the production-package unknown-type baseline.
typecheck-scripts:
    uv run basedpyright --project pyproject.toml scripts

# Type-check tests separately so production and fixture issues stay easy to read.
typecheck-tests:
    uv run basedpyright --project pyproject.toml tests --warnings

# Scan for dead code with vulture.
dead-code:
    uv run vulture

# Check declared dependencies against imported modules.
dependency-lint:
    uv run deptry .

# Auto-fix Ruff findings with safe fixes only, then format.
fix:
    uv run ruff check --preview --fix src scripts tests
    uv run ruff format --no-preview src scripts tests

# Static quality gate.
check: fmt-check lint preview-complexity-lint print-lint typecheck typecheck-scripts typecheck-tests module-boundaries actionlint openapi-contract compile dead-code dependency-lint

# Unit tests.  Docker-marked packaging tests are excluded by default addopts.
unit:
    uv run pytest -q -n auto

# Packaging-policy tests that need a reachable Docker daemon.
docker-tests:
    uv run pytest -q -m docker

# Test coverage report.
coverage:
    uv run pytest --cov=src/enji_guard_cli --cov-report=term-missing

# Human CRAP report over the full suite.
crap:
    uv run pytest --cov=src/enji_guard_cli --cov-report=term-missing --crap --crap-threshold=30 --crap-top-n=30

# One parallel unit-suite invocation that also writes branch coverage and
# enforces CRAP <= 30.  Keep this combined: `verify` used to execute the same
# non-Docker suite once here and again through `unit`.
test-gate:
    coverage_file="$(mktemp /tmp/enji-guard-crap-coverage.XXXXXX.json)"; \
    trap 'rm -f "$coverage_file"' EXIT; \
    uv run pytest -q -n auto --cov=src/enji_guard_cli --cov-report=term-missing --cov-report=json:"$coverage_file"; \
    scripts/crap_gate.py --coverage "$coverage_file" --src src/enji_guard_cli --threshold 30

# Hard CRAP gate: every function must stay at or below CRAP 30.
crap-check: test-gate

# Validate Dockerfile and Compose files without running containers.
docker-check:
    package_version="$(uv run python -c 'from importlib.metadata import version; print(version("enji-guard-cli"))')"; \
    source_commit="$(git rev-parse HEAD)"; \
    PACKAGE_VERSION="$package_version" SOURCE_COMMIT="$source_commit" docker compose config --quiet
    docker build --check .

# Build the Docker image.
docker-build: docker-check
    uv sync --frozen --reinstall-package enji-guard-cli
    package_version="$(uv run python -c 'from importlib.metadata import version; print(version("enji-guard-cli"))')"; \
    source_commit="$(git rev-parse HEAD)"; \
    docker build \
        --build-arg "PACKAGE_VERSION=${package_version}" \
        --build-arg "SOURCE_COMMIT=${source_commit}" \
        -t enji-guard-cli:local .

# Recreate the local Docker service.
docker-up: docker-build
    package_version="$(uv run python -c 'from importlib.metadata import version; print(version("enji-guard-cli"))')"; \
    source_commit="$(git rev-parse HEAD)"; \
    PACKAGE_VERSION="$package_version" SOURCE_COMMIT="$source_commit" docker compose up -d --force-recreate --remove-orphans --wait --wait-timeout 90

# Build distribution artifacts into a caller-selected directory.
package-build out_dir="dist":
    uv build --clear --out-dir "{{out_dir}}"
    rm -f "{{out_dir}}/.gitignore"

# Install built artifacts in clean Python 3.14 environments and exercise both
# the dependency-light CLI and the opt-in MCP service.
package-check:
    artifact_dir="$(mktemp -d /tmp/enji-guard-package.XXXXXX)"; \
    trap 'rm -rf "$artifact_dir"' EXIT; \
    just package-build "$artifact_dir"; \
    uv run python -m scripts.package_contract "$artifact_dir"

# Full local gate for agents before claiming completion.
verify: check test-gate package-check docker-tests docker-build

# Everything the PR owes release-please, checked before pushing rather than
# after a red CI.  The three validators already existed and already ran in CI;
# nothing surfaced them locally, so each rule was learned once by breaking it.
# `title` and `body` default to what the branch already implies, so the common
# case is a bare `just release-check`.
release-check title="" body="":
    #!/usr/bin/env bash
    set -euo pipefail
    base="$(git merge-base origin/main HEAD)"
    count="$(git rev-list --no-merges --count "${base}..HEAD")"
    title="{{title}}"
    if [ -z "${title}" ]; then
        title="$(git log -1 --format=%s "$(git rev-list "${base}..HEAD" | tail -1)")"
    fi
    python3 scripts/validate_pr_title.py --title "${title}"
    python3 -m scripts.validate_pr_commits --base-sha "${base}" --head-sha "$(git rev-parse HEAD)"
    if [ -n "{{body}}" ]; then
        python3 -m scripts.validate_release_notes --body-file "{{body}}" --commit-count "${count}"
    elif [ "${count}" -gt 1 ]; then
        printf 'note: %s commits will squash into one.\n' "${count}" >&2
        printf 'The PR body needs a BEGIN_COMMIT_OVERRIDE block; re-run with body=<file> to check it.\n' >&2
        exit 1
    fi

# Show release, release PR, workflow, and published image status.
release-status:
    scripts/release_status.py

# Read-only smoke against a running Docker service.  The empty project value
# intentionally means account-wide selection; no mutating operation is used.
release-smoke repo project="" container="enji-guard-cli" mcp_url="http://127.0.0.1:18082/mcp":
    uv run python -m scripts.release_smoke --repo "{{repo}}" --project "{{project}}" --container "{{container}}" --mcp-url "{{mcp_url}}"

# Recreate the service and verify auth status survives the restart.
release-smoke-recreate repo project="" container="enji-guard-cli" compose_file="docker-compose.yml":
    uv run python -m scripts.release_smoke --repo "{{repo}}" --project "{{project}}" --container "{{container}}" --compose-file "{{compose_file}}" --recreate --auth-persistence

# Explicitly opt into the reversible project mutation smoke.  The script
# generates a unique reserved project name and cleans up only an exact create.
release-smoke-mutations container="enji-guard-cli":
    uv run python -m scripts.release_smoke_mutations --enable --container "{{container}}"

# Bounded repeated health/status/MCP probes with a failure budget.
release-smoke-soak repo duration="300" interval="30" max_failures="0" project="" container="enji-guard-cli":
    uv run python -m scripts.release_smoke_soak --repo "{{repo}}" --project "{{project}}" --container "{{container}}" --duration "{{duration}}" --interval "{{interval}}" --max-failures "{{max_failures}}"

# Credentialless contract against a caller-supplied local image.  The target
# creates a unique hardened container and always removes it on exit.
release-contract image="enji-guard-cli:local" timeout="20":
    uv run python -m scripts.release_contract --image "{{image}}" --timeout "{{timeout}}"
