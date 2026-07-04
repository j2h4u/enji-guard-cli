# Contributing

This repository is Docker-first and agent-oriented. Keep changes small,
scenario-shaped, and verified through the shared gate.

## Change Intake

Before editing, identify:

- scope: core, CLI, MCP, Enji adapter, auth, Docker, CI, docs, or tests;
- user workflow affected;
- acceptance criteria;
- docs, OpenAPI, tests, or import-linter contracts that must change together.

## Acceptance

A change is ready when:

- behavior is covered by focused tests or an existing contract test;
- CLI/MCP output remains intentional and documented when the surface changes;
- `just verify` passes;
- runtime-sensitive work is checked in Docker, not only in source.

## Handoff

Leave durable context only when it changes future work:

- update `README.md` for user/operator workflow changes;
- update `AGENTS.md` for developer/QA/Ops rules;
- update `README.md` for CLI/MCP ontology or architectural decisions;
- do not keep temporary investigation backlogs after they are resolved.
