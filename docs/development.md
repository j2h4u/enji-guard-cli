# Development

Rules for changing this codebase. [AGENTS.md](../AGENTS.md) links here and
keeps only what an agent must know before reading anything; this file is what
you pull once the task is actually to modify the product.

Related: [decisions.md](decisions.md) for architectural invariants and why they
hold, [CONTRIBUTING.md](../CONTRIBUTING.md) for change intake and handoff,
[deployment.md](deployment.md) for running the built image.

## Toolchain

- Use `uv` only. Keep `uv.lock` current; use hardlink mode outside Docker.
- Keep runtime tuning in frozen settings dataclasses, not env. Env is for
  credential and security ingress only.
- Treat the tach module graph as architecture policy, not style advice. A tach
  failure is an architectural regression, not a lint nit.

## Architecture constraints

- Keep CLI and MCP thin. Put Enji and auth behavior behind the shared core/API
  layer.
- MCP is curated read-only context, not a CLI mirror. Keep it focused on
  portfolio overview and repository report reading. Do not expose auth
  bootstrap, auth-file paths, project/repo writes, scheduling, email settings,
  or other operator controls as MCP tools. MCP server code must use the narrow
  MCP facade, not the broad core facade.
- Cookie auth is temporary. Keep bearer/API-token support first-class.
- Never print secrets. Store credentials only in the configured auth file.

## Product behavior

These are contracts the CLI owes its users, not implementation preferences.

### Mutation safety

- Keep mutating batch writes explicit; never infer all-project or all-repo
  scope.
- Mutating CLI commands must be safe to repeat. Return `unchanged`,
  `already_present`, or `already_running` instead of duplicating work.
- Treat `repo add` as idempotent project membership. If the repo is already
  present, continue. It starts recon when baseline diagnostics are not ready;
  the next step is `status REPO`.
- `--all-projects` is the only unbounded scope and, like `project delete` and
  `repo remove`, requires `--yes` from any non-TTY or `--json` caller.

### Catalog authority

- For every report-aware top-level command, fetch `GET /api/ux/catalog` once;
  do not cache or fall back. Treat `curatedActions` as authoritative so newly
  published reports participate automatically. CLI report selectors are action
  key suffixes without `audit.`; recon remains separate.
- Treat catalog `auditAutofixes` as the source for curated autofix variants
  (`actionKey`, `variantKey`, `title`, `description`, `fleetRunbookId`,
  `status`, `sortOrder`). Manage them through canonical `improvement-jobs`
  list/set operations on the operator CLI. The temporary relationships are
  security/vuln-fix, tests/test-writing, and dependency-hygiene/dependency-update;
  pentest is separate. MCP remains read-only, and explicit `--repo REPO`,
  `--all-repos` with `--project`, or `--all-projects` scope is required for
  batch writes. On the command line the selector is the kind alone
  (`vuln-fix`), not the `audit/kind` pair.

### Scheduling

- Keep schedule timezone stored per schedule, run the container with the host
  timezone, and use `schedule auto-time` to restore Enji-assigned run times.
- Audit schedules use `audit-auto-runs/{actionKey}` with the exact action key
  from `curatedActions`; `improvement-jobs` is autofix-only, never an audit
  scheduling fallback. Batch scheduling must remain an explicit client-side
  loop over the selected repositories and audits.

### Reading audits

- Surface stale or mixed report freshness explicitly; never hide it behind
  aggregate status.
- Treat report language as account-wide; do not present redundant per-project
  resolved values as independent settings.
- Audit reads use report history and the selected Fleet task id, so prior
  usable reports remain readable while newer audits run.
- When reports are stale, compare audited and current git SHAs before judging
  relevance. Use relevant stale or partial-ready reports immediately while
  fresh audits run in parallel.
- Enji audits are slow. Do not treat `wait` as a barrier before analysis. After
  `audit start`, run `status`; read and summarize ready reports immediately,
  then check running reports later with sparse polling.
- For the simple question "are Enji audits ready?", run exactly one
  `status REPO` first. Do not call `wait`, `audit summary`, task-level
  diagnostics, or extra cross-checks unless `status` shows a concrete anomaly
  such as excessive runtime, mismatched current/audited SHA, contradictory
  active-run/task state, reconciliation errors, or upstream unavailability.
  Never use `wait --timeout 1s` as a refresh surrogate; `wait` is a blocking
  completion wait, not a status probe.
- `status` and `audit start` do not trust Enji active-runs alone; the service
  keeps a short local started-task ledger and reconciles it with `task-by-id`
  so incomplete active-runs projections do not trigger duplicate starts.

## Gates

`just verify` is the completion gate. It runs Ruff, basedpyright, tach,
Vulture, deptry, OpenAPI contract validation, CRAP <= 30 per function, tests,
and the Docker build.

- Do not weaken, skip, or suppress any of them. A failing check is information
  about the code, not an obstacle to green.
- Update reconstructed OpenAPI, docs, and tests together when API behavior
  changes.
- Acceptance is mutation-based: break the behavior, show the test go red,
  restore it, show green. A test that never failed proves nothing.

## Pull requests and releases

Run `just release-check` before `gh pr create`, and again with `body=<file>`
once the PR description exists. It runs the same three validators CI runs
(`validate_pr_title`, `validate_pr_commits`, `validate_release_notes`), so a
rejected commit message or a missing `BEGIN_COMMIT_OVERRIDE` block is caught
while it is still cheap to fix — amending one commit rather than rewriting
eight.

A Markdown bullet at column 0 in a commit body breaks release-please: it reads
the marker as a commit type, fails to parse, and drops the entire commit from
the changelog without reporting anything. Upstream that failure is silent, so
this repository does not rely on remembering it. `just hooks` installs a
`commit-msg` hook that rejects the message as you write it, naming the offending
line; the same rule runs again over the whole branch in CI. Indent bullets by
two spaces.

One thing no hook can catch: **editing a PR body does not re-run CI.**
`pull_request` fires on opened/synchronize/reopened, so a body-only fix needs a
close/reopen; a plain re-run replays the stale payload and fails again on the
old body.

A multi-commit PR squashes into one, so its title cannot describe everything it
shipped, and CI demands a `BEGIN_COMMIT_OVERRIDE` block from it.
[CONTRIBUTING.md](../CONTRIBUTING.md#release-notes) owns that format and the
two rules release-please does not enforce itself — it is the authority here,
not this file.

The release PR itself is generated by release-please and lands with no checks,
because a bot push does not trigger workflows. Its runs sit in
`action_required` and are approved through the API rather than by asking an
operator to press a button. Do not put it on auto-merge: merging it under
`GITHUB_TOKEN` produces no tag, no image, and no release.
