# Expert Review Backlog

This backlog captures the 2026-07-01 expert-panel review of the current
codebase. It is intentionally actionable: triage from Critical to Low, check
items off as fixes land, and keep design-only notes separate from hard-gate
work.

Review scope:

- Architecture boundaries between core, adapter, CLI, MCP, auth, runtime, and
  transport.
- Static-analysis hard gates and cheap improvements.
- Adversarial bug review for agent-facing workflows.
- Kaizen review for simplicity, DRY, and YAGNI pressure.

Panel:

- System Architect
- QA / Static Analysis
- Adversarial Python Code Reviewer
- Kaizen / Architecture Simplification

## Current Verdict

- [ ] Critical findings: none reported.
- [ ] Overall architecture: mostly clean. Import direction is intentional, and
  `lint-imports` currently keeps all architecture contracts.
- [ ] Main risk: a few policy gaps allow auth/runtime coupling and core/adapter
  coupling to drift.
- [ ] Main engineering pressure: `core.py`, `enji_api.py`, and `auth.py` remain
  broad modules and should be split by responsibility when touched.

## Critical

- [ ] No Critical findings were reported by the panel.

## High

- [x] Fix report status/wait handling for nested active-run `task.actionKey`.
  - Source: Adversarial Python Code Reviewer.
  - Evidence: `src/enji_guard_cli/core_impl/repo_status.py` handles nested
    `task.actionKey` for duplicate-start checks, but active-run grouping for
    status/wait only uses top-level `actionKey`.
  - Impact: `status` and `wait` can report a report audit as missing/complete
    while a rerun is active. Agents may stop waiting too early and read stale
    reports.
  - Action: centralize active-run action-key extraction and use it in both
    `active_runs_for_action` and `active_runs_by_action_map`.
  - Tests: report status/wait with active runs that only have nested
    `task.actionKey`.

- [x] Make background auto-refresh a no-op for bearer/API-token credentials.
  - Sources: System Architect, Adversarial Python Code Reviewer.
  - Evidence: Docker runs `enji-guard run`; runtime starts auto-refresh
    unconditionally; refresh rejects non-cookie credentials.
  - Impact: future API-token deployments will emit recurring misleading
    refresh failures and violate the “API token first-class” design.
  - Action: start auto-refresh only when stored credential type is cookie, or
    make `start_auto_refresh_task` return `None` for bearer credentials.
  - Tests: bearer auth service startup does not attempt cookie refresh or log
    refresh failure.

## Medium

- [x] Route CLI auth commands through the core facade.
  - Sources: System Architect, Kaizen / Architecture Simplification.
  - Evidence: `src/enji_guard_cli/cli.py` imports `enji_guard_cli.auth`
    directly for import-cookie, import-token, refresh, and status.
  - Impact: CLI is not fully thin; auth behavior can leak around shared core.
  - Action: expose core-level auth use cases and make CLI call those.
  - Architecture gate: add import-linter contract forbidding `cli` and
    `mcp_server` from importing `auth`; allow `runtime` to own supervisor
    refresh.

- [x] Reject malformed GitHub repo slugs for mutating commands.
  - Source: Adversarial Python Code Reviewer.
  - Evidence: `parse_github_repo("owner/name/extra")` currently returns
    `("owner", "name")`.
  - Impact: `repo connect` can target the wrong repository instead of failing.
  - Action: require exactly two non-empty path segments: `OWNER/NAME`.
  - Tests: reject `owner/name/extra`, `/name`, `owner/`, empty strings, and
    empty segments.

- [x] Improve partial-state reporting for two-step project mutations.
  - Source: Adversarial Python Code Reviewer.
  - Evidence: project create writes Fleet then UX; project delete removes UX
    then Fleet.
  - Impact: if the second call fails, Enji can be left with Fleet/UX mismatch
    and the CLI only reports the failing request.
  - Action: prefer an atomic backend endpoint if discovered; otherwise surface
    a partial-state error with project id and completed step. Add rollback only
    if the backend makes it safe.
  - Tests: UX failure after Fleet create; Fleet failure after UX delete.

- [x] Move generic JSON type aliases out of `enji_api.py`.
  - Sources: System Architect, Kaizen / Architecture Simplification.
  - Evidence: `core_impl.models` and related core helpers depend on adapter
    module `enji_api` for `JsonValue` / `JsonObjectPayload`.
  - Impact: pure core/domain transforms depend on adapter-owned vocabulary.
  - Action: create a neutral JSON/types module and update imports.
  - Architecture gate: add import-linter contract keeping pure `core_impl`
    helpers away from `enji_api` after the move.

- [ ] Split `core.py` by use case while keeping `core.py` as the public facade.
  - Sources: System Architect, Kaizen / Architecture Simplification.
  - Evidence: `core.py` owns selector resolution, repo status orchestration,
    report reads, schedule/email mutation, and audit task-body construction.
  - Impact: the facade is becoming a use-case pile; future changes will
    increase coupling.
  - Candidate modules:
    - `core_impl/targets.py`: project/repo resolution and write-scope target
      selection.
    - `core_impl/report_reads.py`: report selection and readable/unavailable
      report payloads.
    - `core_impl/audit_runs.py`: recon/report start orchestration and Fleet task
      body assembly.
    - `core_impl/write_settings.py`: schedule/email fanout over explicit write
      scopes.
  - Rule: move one responsibility at a time; do not introduce service classes
    or containers.
  - Progress: report-read selection and snapshot materialization moved to
    `core_impl/report_reads.py`; the helper module receives snapshot IO as an
    explicit dependency and does not import the adapter directly.
  - Progress: project/repo target resolution moved to `core_impl/targets.py`;
    `core.py` still owns write-scope policy and Enji API calls.

- [x] Split `enji_api.py` into request mechanics and endpoint wrappers.
  - Source: Kaizen / Architecture Simplification.
  - Evidence: `enji_api.py` mixes endpoint specs, session loading, refresh
    retry, response status mapping, parsers, and domain wrappers.
  - Impact: adapter behavior is hard to reason about and harder to test in
    isolation.
  - Candidate module: `enji_api_impl/client.py` for session loading, request
    execution, refresh retry, and response status mapping.
  - Rule: do not generate CLI/MCP from OpenAPI and do not split all endpoints
    in one pass.
  - Architecture gate: `enji_api_impl` is below the `enji_api` facade; core and
    entrypoints may not import it directly.

- [x] Split durable auth from temporary cookie machinery.
  - Source: Kaizen / Architecture Simplification.
  - Evidence: `auth.py` combines credential models, store, status, refresh,
    locks, cookie parsing/deletion, and supervisor loop.
  - Impact: temporary cookie hack and future bearer/API-token path are too
    interleaved.
  - Candidate modules:
    - `auth_impl/store.py`: auth-file load/write/fsync and credential storage.
    - `auth_impl/cookies.py`: cookie normalization, Set-Cookie merge,
      deletion/persistability logic.
  - Rule: bearer/API-token support remains first-class.
  - Architecture gate: `auth_impl` is below the auth facade and has a dedicated
    purity contract.

- [ ] Keep `OPERATION_SPECS` from becoming a universal command registry.
  - Source: Kaizen / Architecture Simplification.
  - Evidence: it currently mixes CLI names, MCP names, summaries, and
    sync/async executors.
  - Impact: one registry can blur CLI and MCP ontology even though their
    surfaces are intentionally different.
  - Action: either narrow it to MCP/read-tool capabilities or keep only shared
    operation metadata that truly belongs to both surfaces.

- [ ] Make report audit definitions non-nullable where possible.
  - Source: Kaizen / Architecture Simplification.
  - Evidence: audit ontology uses duplicated enums plus nullable `route_slug`
    and `job_kind` to model recon versus report audits.
  - Impact: report-audit code carries nullable checks for values that should be
    guaranteed on report audits.
  - Action: separate recon metadata from report audit metadata or introduce a
    report-audit definition with non-null job kind and route slug.

- [ ] Revisit cookie-session 403 refresh behavior.
  - Source: Adversarial Python Code Reviewer.
  - Evidence: `_should_refresh` refreshes on `401` or `403` for cookie
    sessions, even when the response may be a permission error rather than auth
    invalidation.
  - Impact: unnecessary refresh calls can hide real authorization errors and add
    noise.
  - Action: refresh on `AUTH_INVALID` / auth-required payloads; decide whether
    bare `403` should refresh.
  - Tests: cookie-session permission 403 does not refresh unless payload is
    auth-invalid/auth-required.

## Low

- [ ] Treat module size as design pressure, not as a lint gate.
  - Source: QA / Static Analysis.
  - Evidence: `core.py`, `enji_api.py`, `auth.py`, and `cli.py` are large, but
    file length alone is not a correctness signal.
  - Action: keep splitting when a use case is touched; do not add a hard file
    length gate.

- [ ] Keep MCP curated and read-mostly.
  - Sources: System Architect, Kaizen / Architecture Simplification.
  - Evidence: current MCP tests and docs intentionally avoid full CLI parity.
  - Action: preserve the allowlist model. Do not mirror advanced CLI write/admin
    commands into MCP by default.

- [ ] Keep OpenAPI as adapter contract, not as CLI/MCP generator.
  - Sources: System Architect, Kaizen / Architecture Simplification.
  - Action: use OpenAPI to validate lower-level Enji adapter behavior. Do not
    generate agent surfaces from it.

- [ ] Keep visible CLI inventory and raw-plumbing exclusion tests.
  - Source: Kaizen / Architecture Simplification.
  - Evidence: `tests/test_cli_surface_contract.py` protects the public workflow
    surface.
  - Action: update intentionally when CLI ontology changes; do not bypass.

- [ ] Keep transport secret-leak tests.
  - Source: QA / Static Analysis.
  - Evidence: transport logging tests verify path-only logging and no query or
    header secret leakage.
  - Action: preserve and extend these tests as telemetry grows.

## Hard-Gate Improvements

- [x] Add explicit Ruff preview complexity rules: `PLR0914`, `PLR0916`,
  `PLR0917`.
  - Source: QA / Static Analysis.
  - Rationale: current `PLR09` with `explicit-preview-rules = true` does not
    catch every preview rule by prefix.
  - Verified by expert: repo passes
    `uv run ruff check --preview --select PLR0914,PLR0916,PLR0917 src scripts tests`.

- [x] Add production-only print/debug leakage lint: `T20` for
  `src/enji_guard_cli`.
  - Source: QA / Static Analysis.
  - Rationale: CLI should write through Typer/rendering paths, not raw
    production `print()`.
  - Scope: avoid applying to scripts unless desired.

- [x] Enable Ruff unused-argument lint `ARG` after cleanup.
  - Source: QA / Static Analysis.
  - Scope: production code only; tests ignore `ARG` because fake callbacks
    intentionally preserve call signatures.
  - Action: removed the unused `options` parameter from
    `core_impl/repo_status.py:report_wait_payload` and added `ARG` to Ruff.

- [x] Expand Vulture paths to include `scripts`.
  - Source: QA / Static Analysis.
  - Current config covers `src/enji_guard_cli` and `tests`.
  - Verified by expert: `uv run vulture src/enji_guard_cli scripts tests` is
    clean.

- [x] Harden pytest config.
  - Source: QA / Static Analysis.
  - Add: `--strict-config --strict-markers -W error`.
  - Add: `xfail_strict = true`.
  - Verified by expert: all current tests passed with those flags.

- [x] Add Docker static checks.
  - Source: QA / Static Analysis.
  - Candidate commands:
    - `docker compose config --quiet`
    - `docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet`
    - `docker build --check .`
  - Verified by expert: all three passed.

- [x] Add OpenAPI schema validation in addition to the custom semantic
  validator.
  - Source: QA / Static Analysis.
  - Candidate dependency: `openapi-spec-validator`.
  - Candidate command: `uv run openapi-spec-validator contracts/enji-openapi.json`.
  - Keep existing `scripts/validate_openapi_contract.py`.

- [x] Consider scoped Ruff security lint.
  - Source: QA / Static Analysis.
  - Candidate command:
    `uv run ruff check --preview --select S --ignore S105 src/enji_guard_cli scripts`.
  - Note: full `S` over tests is noisy because pytest assertions trigger `S101`.
  - Decision: enable production/script `S` lint, ignore noisy `S101`/`S105`,
    and exclude tests from `S` because fixtures intentionally contain unsafe
    literals such as `0.0.0.0` and `/tmp`.

## Current Gate Coverage To Preserve

- [ ] Ruff format/check, including stable complexity/refactor rules such as
  `C90`, `PLR0911`, `PLR0912`, `PLR0913`, `PLR0915`.
- [ ] basedpyright for production/scripts and tests.
- [ ] import-linter contracts for core independence, entrypoint thinness, HTTP
  client isolation, framework isolation, and settings purity.
- [ ] Vulture and deptry.
- [ ] actionlint.
- [ ] custom OpenAPI semantic validator.
- [ ] CRAP hard gate: every function must stay at or below CRAP 30.
- [ ] unit tests.
- [ ] Docker build in `just verify`.
- [ ] GitHub CI split into quality, tests, CRAP, and Docker build.
- [ ] CodeQL, dependency review, and Dependabot.

## Do Not Do Yet

- [ ] Do not generate CLI/MCP from OpenAPI.
- [ ] Do not make MCP parity with the advanced CLI.
- [ ] Do not introduce service classes, repositories, dependency containers, or
  provider hierarchies.
- [ ] Do not add compatibility shims while splitting. This repo has no
  backwards-compatibility obligation yet.
- [ ] Do not split every large file in one pass.
- [ ] Do not lower CRAP or complexity thresholds before there is evidence the
  default gate is insufficient.
- [ ] Do not try to statically lint all secret/log correctness. Use tests and
  review for semantic leak risks.

## Suggested Triage Order

1. [x] Fix nested active-run `task.actionKey` in status/wait.
2. [x] Make bearer/API-token auth skip cookie auto-refresh.
3. [x] Reject malformed GitHub repo slugs.
4. [x] Add the cheap hard-gate improvements that already pass.
5. [x] Move CLI auth commands behind core and tighten import-linter.
6. [x] Move neutral JSON types out of `enji_api.py` and tighten import-linter.
7. [ ] Split `core.py` by use case.
8. [x] Split auth cookie/store responsibilities.
9. [x] Split `enji_api.py` request mechanics from endpoint wrappers.
10. [x] Improve project create/delete partial-state reporting.
