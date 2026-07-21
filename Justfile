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

# Check import-layer architecture contracts.
import-contracts:
    uv run lint-imports

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

# Run the canonical static type checker on production code.
typecheck:
    uv run basedpyright src/enji_guard_cli scripts

# Type-check tests separately so production and fixture issues stay easy to read.
typecheck-tests:
    uv run basedpyright tests --warnings

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
check: fmt-check lint preview-complexity-lint print-lint typecheck typecheck-tests import-contracts actionlint openapi-contract compile dead-code dependency-lint

# Unit tests.
unit:
    uv run pytest -q -n auto

# Test coverage report.
coverage:
    uv run pytest --cov=src/enji_guard_cli --cov-report=term-missing

# Human CRAP report over the full suite.
crap:
    uv run pytest --cov=src/enji_guard_cli --cov-report=term-missing --crap --crap-threshold=30 --crap-top-n=30

# Hard CRAP gate: every function must stay at or below CRAP 30.
crap-check:
    coverage_file="$(mktemp /tmp/enji-guard-crap-coverage.XXXXXX.json)"; \
    trap 'rm -f "$coverage_file"' EXIT; \
    uv run pytest --cov=src/enji_guard_cli --cov-report=json:"$coverage_file"; \
    scripts/crap_gate.py --coverage "$coverage_file" --src src/enji_guard_cli --threshold 30

# Validate Dockerfile and Compose files without running containers.
docker-check:
    docker compose config --quiet
    docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet
    docker build --check .

# Build the Docker image.
docker-build: docker-check
    package_version="$(uv run python -c 'from importlib.metadata import version; print(version("enji-guard-cli"))')"; \
    source_commit="$(git rev-parse HEAD)"; \
    docker build \
        --build-arg "PACKAGE_VERSION=${package_version}" \
        --build-arg "SOURCE_COMMIT=${source_commit}" \
        -t enji-guard-cli:local .

# Recreate the local Docker service.
docker-up:
    docker compose up -d --force-recreate --remove-orphans --wait --wait-timeout 90

# Recreate the Docker service in source bind-mount dev mode.
dev-up:
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate --remove-orphans --wait --wait-timeout 90

# Reload bind-mounted source after local code changes.
dev-reload:
    docker compose -f docker-compose.yml -f docker-compose.dev.yml restart enji-guard-cli

# Full local gate for agents before claiming completion.
verify: check crap-check unit docker-build

# Show release, release PR, workflow, and published image status.
release-status:
    scripts/release_status.py

# Read-only smoke against a running Docker service.  The empty project value
# intentionally means account-wide selection; no mutating operation is used.
release-smoke repo project="" container="enji-guard-cli" mcp_url="http://127.0.0.1:8001/mcp":
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
