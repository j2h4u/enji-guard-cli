# Agent Rules

Python 3.14 Docker service exposing Enji Guard through core code, CLI, and MCP.
README.md carries the user-facing CLI model and workflows.
docs/decisions.md captures the current architectural decisions and invariants.
CONTRIBUTING.md carries change intake, acceptance, and handoff rules.

## Development

- Use `uv` only. Keep `uv.lock` current; use hardlink mode outside Docker.
- Keep CLI and MCP thin. Put Enji/auth behavior behind the shared core/API layer.
- MCP is curated read-only context, not a CLI mirror. Keep it focused on
  portfolio overview and repository report reading. Do not expose auth
  bootstrap, auth-file paths, project/repo writes, scheduling, email settings, or
  other operator controls as MCP tools. MCP server code must use the narrow
  MCP facade, not the broad core facade.
- Treat import-linter as architecture policy, not style advice.
- Keep runtime tuning in frozen settings dataclasses, not env. Env is for
  credential/security ingress only.
- Keep mutating batch writes explicit; never infer all-project or all-repo scope.
- Mutating CLI commands must be safe to repeat. Return `unchanged`,
  `already_present`, or `already_running` instead of duplicating work.
- Treat `repo add` as idempotent project membership. If the repo is already
  present, continue. It starts recon when baseline diagnostics are not ready;
  the next step is `status REPO`.
- For every report-aware top-level command, fetch `GET /api/ux/catalog` once;
  do not cache or fall back. Treat `curatedActions` as authoritative so newly
  published reports participate automatically. CLI report selectors are action
  key suffixes without `audit.`; recon remains separate.
- Treat catalog `auditAutofixes` as the source for curated autofix variants
  (`actionKey`, `variantKey`, `title`, `description`, `fleetRunbookId`,
  `status`, `sortOrder`). Manage them through canonical
  `improvement-jobs` list/set operations on the operator CLI. The temporary
  relationships are security/vuln-fix, tests/test-writing, and
  dependency-hygiene/dependency-update; pentest is separate. MCP remains
  read-only, and explicit `REPO`, `--all-repos` with `--project`, or
  `--all-projects` scope is required for batch writes.
- Keep schedule timezone stored per schedule, run the container with the host
  timezone, and use `schedule auto-time` to restore Enji-assigned run times.
- Audit schedules use `audit-auto-runs/{actionKey}` with the exact action key
  from `curatedActions`; `improvement-jobs` is autofix-only, never an audit
  scheduling fallback. Batch scheduling must remain an explicit client-side
  loop over the selected repositories and audits.
- Surface stale/mixed report freshness explicitly; never hide it behind aggregate status.
- Treat report language as account-wide. After changing it, verify the effective
  run language returned for each project.
- Before starting fresh audits, save/read every currently available report you
  may need. Starting audits can temporarily hide old snapshots behind running state.
- When reports are stale, compare audited and current git SHAs before judging
  relevance. Use relevant stale or partial-ready reports immediately while fresh
  audits run in parallel.
- Enji audits are slow. Do not treat `wait` as a barrier before analysis. After
  `audit start`, run `status`; read and summarize ready reports immediately,
  then check running reports later with sparse polling.
- `status` and `audit start` do not trust Enji active-runs alone; the service
  keeps a short local started-task ledger and reconciles it with `task-by-id`
  so incomplete active-runs projections do not trigger duplicate starts.
- Cookie auth is temporary. Keep bearer/API-token support first-class.
- Never print secrets. Store credentials only in the configured auth file.

## QA

- `just verify` is the completion gate.
- Do not weaken, skip, or suppress Ruff, types, import contracts, Vulture,
  deptry, OpenAPI, CRAP, tests, or Docker build.
- Update reconstructed OpenAPI, docs, and tests together when API behavior changes.

## Ops

- Docker is the runtime. Verify the running container, not just source.
- Local development compose builds `enji-guard-cli:local`; deployment should
  pull `ghcr.io/j2h4u/enji-guard-cli` with `deploy/docker-compose.ghcr.yml`.
- Recreate the service after runtime, env, image, or auth-mount changes.
- Application telemetry lives in `~/.config/enji-guard/logs/telemetry.jsonl`;
  CLI/MCP agent journey events use the shared telemetry layer. stdout/stderr
  belong to CLI results, progress, and CLI errors.
- The container runs `enji-guard run`: supervisor owns MCP, background cookie
  refresh, and backend readiness heartbeat as sibling tasks. MCP must not own
  refresh.
- Docker health is service readiness: local MCP plus cached authenticated Enji
  backend readiness. Heartbeat records auth/backend failures; it must not call
  refresh directly.
- The host auth file must stay writable because Enji rotates refresh cookies.
- Cookie bootstrap is one-time: refresh in the browser first, then import the
  current cookie state. Prefer a `Cookie` header from any Fleet request made
  after refresh. If using the refresh request itself, merge its response
  `Set-Cookie` values; its request `Cookie` has the old refresh token.
- After bootstrap, prove Docker refresh works: container `auth refresh`, CLI auth
  smoke, and `enji_auth_auto_refresh_succeeded` in logs. MCP tools should either
  work through the configured service auth or return clear auth errors.
