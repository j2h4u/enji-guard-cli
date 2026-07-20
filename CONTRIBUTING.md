# Contributing

This repository is Docker-first and agent-oriented. Keep changes small,
scenario-shaped, and verified through the shared gate.

## Change Intake

Before editing, identify:

- scope: Audit, Portfolio, Application, CLI, MCP, Enji gateway, auth, runtime, Docker, CI, docs, or tests;
- user workflow affected;
- acceptance criteria;
- whether the change crosses Audit/Portfolio/Application/infrastructure boundaries;
- docs, OpenAPI, tests, or import-linter contracts that must change together;
- whether the mutation must remain explicitly scoped and idempotent (`unchanged`, `already_present`, `already_running`).

## Acceptance

A change is ready when:

- behavior is covered by focused tests or an existing contract test;
- CLI/MCP output remains intentional and documented when the surface changes;
- CLI and MCP stay thin and continue to rely on the shared Application/core layer;
- `just verify` passes;
- runtime-sensitive work is checked in Docker, not only in source;
- auth/runtime changes are validated with the running service (`auth status`, `health --ready`, telemetry when relevant), not with ad hoc refresh commands.

## Handoff

Leave durable context only when it changes future work:

- update `README.md` for user/operator workflow and CLI/MCP ontology changes;
- update `AGENTS.md` for developer/QA/Ops rules;
- update `docs/decisions.md` for architectural decisions and invariants;
- keep schedule, catalog-driven audit/autofix behavior, and auth/runtime wording aligned across docs when those workflows change;
- do not keep temporary investigation backlogs after they are resolved.
