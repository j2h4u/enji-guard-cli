# Changelog

## [0.1.0] - 2026-07-03

### Features

- Add a Docker-first Enji Guard service with CLI, MCP runtime, persistent cookie
  refresh, and telemetry.
- Add repository, project, report, schedule, email, and wait workflows for
  agent-operated Enji Guard usage.
- Add reconstructed Enji OpenAPI contract coverage and strict local/CI quality
  gates.

### Fixes

- Handle unavailable report snapshots in batch reads without aborting readable
  reports.
- Keep the Docker image default loopback-safe while compose deployments publish
  MCP only on host loopback.

[0.1.0]: https://github.com/j2h4u/enji-guard-cli/releases/tag/v0.1.0
