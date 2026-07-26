# Contributing

This repository is Docker-first and agent-oriented. Keep changes small,
scenario-shaped, and verified through the shared gate.

## Change Intake

Before editing, identify:

- scope: Audit, Portfolio, Application, CLI, MCP, Enji gateway, auth, runtime, Docker, CI, docs, or tests;
- user workflow affected;
- acceptance criteria;
- whether the change crosses Audit/Portfolio/Application/infrastructure boundaries;
- docs, OpenAPI, tests, or the tach module graph that must change together;
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

The override is a `BEGIN_COMMIT_OVERRIDE` / `END_COMMIT_OVERRIDE` block in the
PR description. Release-please replaces the entire squash commit message with
its contents, so the block is what the changelog is built from. CI demands one
from any PR that squashes more than one commit, because the merge keeps a single
subject and silently discards the rest — that is how v3.0.0 turned 98 commits
into one changelog line.

Two formatting rules matter, and release-please enforces neither: the
Conventional Commit type must sit at column 0 with entries separated by blank
lines, and a `BREAKING CHANGE:` note must carry its bullets on the very next
line. A bulleted list of subjects, which is what GitHub's default squash body
looks like, parses as one entry; a blank line under `BREAKING CHANGE:` ends the
note and drops everything below it. `scripts/validate_release_notes.py` checks
both in CI.

When a broad user-facing change cannot be understood from the commit subjects,
use that override to tell the short product story. Describe the current concepts and likely user workflows rather than the
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
