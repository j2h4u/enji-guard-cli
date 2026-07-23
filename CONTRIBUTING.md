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
- candidate images pass `just release-contract IMAGE` before publication;
- runtime or release changes pass the read-only authenticated `release-smoke`
  journey, including recreate when auth persistence is in scope;
- auth/runtime changes are validated with the running service (`auth status`, `health --ready`, telemetry when relevant), not with ad hoc refresh commands; terminal cookie states are recovered by an explicit browser credential import, never a replay or manual refresh.

## Handoff

Leave durable context only when it changes future work:

- update `README.md` for user/operator workflow and CLI/MCP ontology changes;
- update `AGENTS.md` for developer/QA/Ops rules;
- update `docs/decisions.md` for architectural decisions and invariants;
- keep schedule, catalog-driven audit/autofix behavior, and auth/runtime wording aligned across docs when those workflows change;
- keep the v2 auth revision/journal, observer-only boundary, single-host storage contract, and at-least-once telemetry wording aligned across README, decisions, and deployment docs;
- do not keep temporary investigation backlogs after they are resolved.

## Release Notes

Routine fixes and features use their conventional commit subjects as release
notes. Do not edit an unreleased version into `CHANGELOG.md`; release-please
owns version headings, dates, comparison links, and the generated GitHub
Release.

When a broad user-facing change cannot be understood from the commit subjects,
use the implementation PR's release-note override to tell the short product
story. Describe the current concepts and likely user workflows rather than the
refactor history. For example, explain that audits now own run state,
freshness, scores, and findings; that improvements are optional operator work;
and that MCP intentionally exposes only portfolio context and repository audit
reading.

Do not turn this into an exhaustive migration plan. Mention removed or renamed
syntax only when it materially helps a person switch workflows or when the
deterministic `--json` contract used by scripts changed. Keep independent
features, fixes, and performance changes as separate conventional messages so
release-please places them in the configured section. Review the generated
release PR's `CHANGELOG.md` as user documentation before merging it.
