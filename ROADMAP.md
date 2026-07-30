# Roadmap

This project is intentionally small. Its goal is to make Enji Guard usable by
local coding agents through a Dockerized service, a practical CLI, and an MCP
surface.

## Done

- Established the Docker-first runtime and credential bootstrap flow.
- Built the shared core that hides Enji authentication, refresh, retries, rate
  limits, and API details behind stable operations.
- Shaped the CLI into the primary operator surface for agents: repositories,
  projects, audit runs, readiness, freshness, schedules, improvement jobs,
  email preferences, and audit reading.
- Added the curated read-only MCP surface for portfolio overview and repository
  audit reading without operator controls.
- Split Audit, Portfolio, Auth Session, Runtime/Observability, gateway, and
  delivery ownership into enforced bounded contexts.
- Added persistent JSONL telemetry so long-running audit and auth behavior can
  be inspected after restarts. CLI and MCP agent journey events use the same
  telemetry layer. This is currently JSONL-only and is the minimal foundation
  for future external sinks and OpenTelemetry-style export.
- Added release automation, container publishing, and a strict local/CI quality
  gate. Added a release status check for open PRs, the latest release, GHCR
  publication, and recent GitHub Actions.
- Hardened the release pipeline: one CI-gated entry point for container
  publishing, scan before push, and provenance and SBOM attested against the
  digest that consumer tags resolve to.
- Made the audit ledger safe under concurrency, and made audit status honest
  about what it cannot prove: a run without head evidence is reported as
  unverified rather than as work worth waiting for.
- Settled the operator contract: duplicate commands removed, irreversible
  writes require explicit confirmation, and errors carry the upstream status
  instead of a bare failure.
- Replaced the owner-loop HTTP bridge with a pooled synchronous client, which
  is faster at every batch size measured and removed the deadlock-avoidance
  code that surrounded it.
- Made module boundaries the enforced architecture gate through `tach`, which
  declares the whole graph so an undeclared dependency fails; import-linter is
  gone. The suite grew from 428 to over 900 tests behind a coverage floor.
- Pinned the release notes themselves: a squash merge that would silently drop
  what shipped now fails CI instead.
- Made an ambiguous cookie refresh recoverable without an operator. A gateway
  `502` used to be terminal; the refresh loop now asks the backend what the
  rotation actually did and resumes when the held credential is still alive,
  bounded by that credential's own expiry.

## Current State

The product is working for its primary scope: Docker-first Enji Guard access
through a validated CLI and a smaller read-only MCP surface. The supervisor
owns MCP, automatic cookie refresh, and backend readiness. Current work is live
operational hardening for maintenance and future releases.

## Remaining

- Exercise the CLI and supervisor against the live Enji service long enough to
  catch operational regressions before merge.
- Refine MCP audit-reading ergonomics with real agents while keeping the
  surface centered on portfolio overview and concrete repository audits.
- Add one narrow agent-feedback use case. The confirmed `POST /api/ux/feedback`
  endpoint is not exposed by the CLI today; a future operator command may send
  structured signals about confirmed audit quality (false positives, missed
  findings, and autofix usefulness). Keep it a dedicated future Application
  boundary: do not expand the existing Audit or Subscriptions contexts or add a
  broad write surface.
- Add a protected production-deploy workflow for an already published immutable
  GHCR image. The current deployment path is intentionally manual; the next
  step is a `workflow_dispatch` promotion with protected-environment approval,
  health checks, and a documented rollback to the previous digest.
- Add a lightweight active task/spec and handoff template for multi-step agent
  work. Keep it short: scope, user workflow, acceptance criteria, touched
  boundaries, checks run, blockers, and the next action. This is for unfinished
  work and larger changes, not routine PRs.
- Formalize container vulnerability exceptions. Update pinned base/tool images
  when a fixed digest exists; when Debian/Python rows remain unfixed, record a
  deliberate VEX/ignore decision with owner, reason, and review trigger instead
  of letting `ignore-unfixed` hide the risk forever.
- **API-key cutover is the blocker for deleting daemon auth/readiness.** Once
  Enji ships the supported API-key flow, delete—not disable or retain as a
  fallback—the browser-cookie credential variant, refresh FSM, rotation
  journal/outbox, auth-file watcher, supervisor refresh task, and cached backend
  readiness plumbing. In that same change, redefine health for the opt-in MCP
  service or remove readiness from the standalone CLI; do not preserve a
  daemon-auth readiness contract after its owner is gone. No fallback or
  compatibility archaeology survives this cutover.
- **Make the MCP container optional after that cutover.** The default becomes a
  standalone API-key CLI with no Docker, supervisor, or background process,
  packaged for `pip`/`pipx`/`uv tool install`. This package-first stage requires
  a distribution artifact/build/install gate (separate from the Docker-image
  gate), PyPI trusted-publishing setup by its external owner, and an explicit
  Windows/`fcntl` portability decision (portable replacement or declared
  non-Windows support) before release. Anyone who wants the curated MCP surface
  opts into its container.
- **Defer MCP 2026-07-28 and Python SDK v2.** Do not change the dependency now.
  Consider it only after the repository dependency-quarantine exit criteria are
  met (7–14 days, or earlier owner approval; reviewed lock/provenance and
  lifecycle policy). The later upgrade deletes legacy SSE, mount-path,
  `initialize`/`initialized`, and session-header compatibility, then rewrites
  release smoke and protocol-contract coverage around the new protocol.

After that, the project should move into maintenance mode rather than broad
feature development.

## Appendix: Install Modes After API Keys

The product stays one project with more than one way to run it, never a split
into separate CLI and service products.

- **Default: CLI only.** Core plus CLI, authenticated with an API key,
  published as the normal Python package and installed with `pip`, `pipx`, `uv
  tool install`, or equivalent Python tooling. No Docker, no supervisor, no
  background process.
- **Opt-in: MCP container.** The curated read-mostly surface for agents that
  want it, and only for them.

The Docker service exists today because browser-cookie auth needs a process
that keeps rotating credentials. That is its whole justification. After the
API-key cutover, the browser-cookie credential variant and its refresh daemon
are deleted rather than demoted to a fallback; a credential path nobody uses is
one nobody tests. The replacement design must first explicitly retain a
meaningful MCP-service health contract or remove standalone CLI readiness.
