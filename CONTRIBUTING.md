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
- auth/runtime changes are validated with the running service (`auth status`, `health --ready`, telemetry when relevant), not with ad hoc refresh commands.

## Handoff

Leave durable context only when it changes future work:

- update `README.md` for user/operator workflow and CLI/MCP ontology changes;
- update `AGENTS.md` for developer/QA/Ops rules;
- update `docs/decisions.md` for architectural decisions and invariants;
- keep schedule, catalog-driven audit/autofix behavior, and auth/runtime wording aligned across docs when those workflows change;
- do not keep temporary investigation backlogs after they are resolved.

## Release Notes

Routine fixes and features use their conventional commit subjects as release
notes. Do not edit an unreleased version into `CHANGELOG.md`; release-please
owns version headings, dates, comparison links, and the generated GitHub
Release.

When one squash PR changes several user workflows or any public CLI contract,
its PR body must contain a release-note override. Treat it as part of the user
interface: describe outcomes rather than the internal refactor, put every
removal or rename in `BREAKING CHANGE:`, and show its replacement. Keep each
independent feature, fix, or performance change as a separate conventional
message so release-please places it in the configured section.

```text
BEGIN_COMMIT_OVERRIDE
feat(cli)!: make audit the canonical repository workflow

BREAKING CHANGE: `old command` moved to `replacement command`; `removed command`
was removed because the service now performs that work automatically.

feat(cli): add compact scenario-oriented status output
fix(auth): react immediately to imported credentials
perf(cli): pool and bound portfolio reads

Release-As: 1.0.0
END_COMMIT_OVERRIDE
```

Before squash-merging a user-facing release:

- compare the public CLI help and README workflows with the target branch;
- account for added, removed, renamed, and behaviorally changed commands;
- verify both human output and `--json` contracts;
- put the curated override in the implementation PR body;
- run the acceptance and release-smoke gates above;
- review the generated release PR's `CHANGELOG.md` as user documentation before
  merging it.
