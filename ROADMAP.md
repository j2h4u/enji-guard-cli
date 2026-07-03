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
- Added persistent telemetry so long-running report and auth behavior can be
  inspected after restarts.
- Added release automation, container publishing, and a strict local/CI quality
  gate.

## Current State

The product is close to complete for its intended scope. The CLI is the
validated surface and is suitable for daily agent workflows. Docker is the
supported runtime.

## Remaining

- Design the MCP surface around the same product model as the CLI, but keep it
  smaller and mostly read-oriented.
- Implement the MCP tools needed for repository overview, report freshness,
  report summaries, and focused report reading.
- Validate the MCP surface with real agents and adjust only where the workflow
  is unclear or noisy.

After that, the project should move into maintenance mode rather than broad
feature development.
