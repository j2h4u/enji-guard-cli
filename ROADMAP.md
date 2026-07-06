# Roadmap

This project is intentionally small. Its goal is to make Enji Guard usable by
local coding agents through a Dockerized service, a practical CLI, and an MCP
surface.

## Done

- Established the Docker-first runtime and credential bootstrap flow.
- Built the shared core that hides Enji authentication, refresh, retries, rate
  limits, and API details behind stable operations.
- Shaped the CLI into the primary operator surface for agents: repositories,
  projects, report runs, readiness, freshness, schedules, email preferences,
  and report reading.
- Added persistent JSONL telemetry so long-running report and auth behavior can
  be inspected after restarts. CLI and MCP agent journey events use the same
  telemetry layer. This is currently JSONL-only and is the minimal foundation
  for future external sinks and OpenTelemetry-style export.
- Added release automation, container publishing, and a strict local/CI quality
  gate. Added a release status check for open PRs, the latest release, GHCR
  publication, and recent GitHub Actions.

## Current State

The product is working for its primary scope: Docker-first Enji Guard access
through a validated CLI for daily agent workflows. Docker is the supported
runtime. The remaining product work is concentrated in the smaller read-only
MCP surface and optional future install modes.

## Remaining

- Finish the MCP surface around the same product model as the CLI, but keep it
  smaller, read-only, and free of operator/control-plane details.
- Refine MCP report-reading ergonomics with real agents while keeping the
  surface centered on portfolio overview and concrete repository reports.
- Validate the MCP surface with real agents and adjust only where the workflow
  is unclear or noisy.
- Explore modular install modes so the tool can be used as CLI-only when API
  tokens make background cookie refresh unnecessary.

After that, the project should move into maintenance mode rather than broad
feature development.

## Appendix: Modular Install Notes

The product should remain one project with multiple ways to run it, not a split
between separate CLI and service products.

- Base install: CLI and core only, suitable for API-token auth and direct agent
  use through `uv`, `uvx`, or a host wrapper in `/usr/local/bin`.
- MCP install: optional MCP dependencies and tools for agents that need a
  curated read-mostly service surface.
- Docker service: full runtime for MCP plus temporary cookie refresh while
  browser-cookie auth is still needed.

When Enji API tokens are available, CLI-only usage should not require Docker,
MCP, supervisor tasks, or background refresh.
