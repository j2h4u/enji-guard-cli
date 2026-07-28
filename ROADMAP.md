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
- **When Enji Guard ships API keys, delete the refresh daemon.** Not make it
  optional, not keep it as a fallback: browser-cookie auth is the only reason
  it exists, and an API key removes that reason entirely. The auth state
  machine, the rotation outbox, the supervisor task that owns refresh, and the
  readiness plumbing that watches it all go with it.
- **Make the MCP container optional in the same move.** The default becomes a
  standalone CLI with no Docker, no supervisor and no background process —
  packaged as a normal Python distribution and installable with `pip`/`pipx`
  without any container bootstrap. Anyone who wants the curated MCP surface for
  agents opts into the container; nobody has to run one to use the tool.

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
that keeps rotating credentials. That is its whole justification. Once an API
key removes it, the refresh daemon is deleted rather than demoted to a
fallback: a credential path nobody uses is one nobody tests, and it would keep
the supervisor, the rotation state machine and their readiness plumbing alive
for no one.
