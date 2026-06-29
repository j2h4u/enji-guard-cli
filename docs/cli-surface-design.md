# CLI Surface Design

This CLI is a workflow surface for a tech-lead agent. It must not mirror Enji's
frontend API.

## Goals

- Keep commands close to agent tasks: inspect state, connect repos, start work,
  wait, read reports, manage schedules and email preferences.
- Hide frontend implementation details behind core use cases.
- Accept project and repo names where they are unambiguous.
- Keep read commands broad and safe; keep write commands explicit.

## Public Ontology

Primary nouns:

- `project`: Enji workspace/project inventory.
- `repo`: GitHub repository inventory, target resolution, and connection.
- `recon`: preliminary repository diagnostics.
- `audit`: report-producing checks.
- `report`: generated report content.
- `schedule`: recurring audit settings.
- `email`: report completion email preferences.
- `status`: runtime snapshot across projects/repos/tasks.
- `wait`: polling for long-running work.

Canonical report audits:

- `security`
- `ai-readiness`
- `tests`
- `tech-health`
- `deps`
- `cognitive-debt`
- `dead-code`

`recon` is not a report audit. It is a separate preliminary diagnostic phase.

## Command Shape

```text
enji-guard health
enji-guard access
enji-guard run
enji-guard auth status
enji-guard auth refresh
enji-guard auth import-cookie --stdin
enji-guard auth import-token --stdin

enji-guard project list

enji-guard repo list [--sort default|name|weakest|overall|latest-report]
enji-guard repo resolve REPO
enji-guard repo connect OWNER/NAME

enji-guard status [REPO] [--sort default|name|weakest|overall|latest-report]

enji-guard recon start REPO
enji-guard audit start REPO AUDIT...
enji-guard audit start REPO --all

enji-guard wait REPO AUDIT_OR_RECON

enji-guard report list [--selector SELECTOR]
enji-guard report read REPO [AUDIT...] [--all] [--json]
enji-guard report show REPO AUDIT [--json]

enji-guard schedule list REPO
enji-guard schedule set REPO AUDIT --freq FREQ [--day DAY...] [--at auto|HH:MM|HH:MM@TZ]
enji-guard schedule disable REPO AUDIT

enji-guard email list [REPO]
enji-guard email set [REPO] [--manual on|off|keep] [--auto on|off|keep]
```

Project filtering is a global option:

```text
enji-guard --project NAME_OR_ID status
enji-guard --project NAME_OR_ID audit start OWNER/NAME --all
enji-guard --project NAME_OR_ID email set --auto off
```

CLI output is human-readable by default. Use `--json` only when automation needs
the raw machine contract.

## Resolution Rules

- Global `--project` accepts an Enji project id or an exact project name.
- Repo selectors accept an Enji repo id or `owner/name`.
- Read commands may omit `--project`; they show all projects or all matching
  repos.
- Write commands may omit `--project` only when the target repo is unambiguous.
- `email set` without `REPO` is an explicit batch write over every repo in the
  current project filter; without `--project`, it spans all projects.
- Ambiguous targets fail with `BAD_SELECTOR` and include candidates.
- There is no default project.
- No fuzzy matching in write commands.
- `report list` is compact inventory; `report read` returns report content.
- `report read REPO` defaults to all currently ready reports for that repo.
- `email` preferences apply to all report audits. `--manual` controls mail
  after manual runs; `--auto` controls mail after scheduled automatic runs.
- Repo inventory/status sorting is optional. `weakest` and `overall` sort lower
  scores first; `latest-report` sorts newer report activity first. Repos
  without the selected signal stay last.

## Hidden Details

These concepts belong in core or debug tooling, not the public CLI:

- raw run plumbing
- raw report-link plumbing
- raw history payloads
- raw freshness/rerun payloads
- GitHub installation plumbing
- raw schedule JSON payloads
- `*-all` command variants

Public commands should expose scenario state such as `ready`, `running`,
`missing`, `connected`, `recon_done`, `active_run_count`, and report revision
drift. Repo list and status payloads include Enji scores by default as raw
`scores`, simple `score_grades`, and a compact `score_summary`; there is no
separate score flag.
