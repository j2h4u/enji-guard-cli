set shell := ["bash", "-uc"]
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
openapi-contract:
    scripts/validate_openapi_contract.py

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
check: fmt-check lint typecheck typecheck-tests import-contracts actionlint openapi-contract compile dead-code dependency-lint

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

# Build the Docker image.
docker-build:
    docker build -t enji-guard-cli:local .

# Recreate the local Docker service.
docker-up:
    docker compose up -d --force-recreate --remove-orphans --wait --wait-timeout 90

# Recreate the Docker service in source bind-mount dev mode.
dev-up:
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate --remove-orphans --wait --wait-timeout 90

# Force-refresh the mounted auth file from inside the running dev container.
dev-refresh:
    docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T enji-guard-cli enji-guard auth refresh

# Reload bind-mounted source without rebuilding; refresh auth first.
dev-reload: dev-refresh
    docker compose -f docker-compose.yml -f docker-compose.dev.yml restart enji-guard-cli
    docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T enji-guard-cli enji-guard auth status

# Full local gate for agents before claiming completion.
verify: check crap-check unit docker-build
