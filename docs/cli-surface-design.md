# CLI Surface Design

This CLI is a workflow surface for a tech-lead agent. It must not mirror Enji's
frontend API.

## Goals

- Keep commands close to agent tasks: inspect state, connect repos, start work,
  manage projects, move repos, wait, read reports, manage schedules and email
  preferences.
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
- `wait`: blocking readiness check for all report audits in one repo.

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
enji-guard project create NAME
enji-guard project rename PROJECT NAME
enji-guard project delete PROJECT --yes

enji-guard repo list [--sort default|name|weakest|overall|latest-report]
enji-guard repo resolve REPO
enji-guard repo connect OWNER/NAME
enji-guard repo move REPO --to-project PROJECT

enji-guard status [REPO] [--sort default|name|weakest|overall|latest-report]

enji-guard recon start REPO
enji-guard audit start REPO AUDIT...
enji-guard audit start REPO --all

enji-guard wait REPO

enji-guard report list [--selector SELECTOR]
enji-guard report read REPO [AUDIT...] [--all] [--json]
enji-guard report show REPO AUDIT [--json]

enji-guard schedule list [REPO]
enji-guard schedule set REPO --enabled on|off|keep [--freq FREQ]
enji-guard --project PROJECT schedule set --all-repos --enabled on|off|keep [--freq FREQ]
enji-guard schedule set --all-projects --enabled on|off|keep [--freq FREQ]
enji-guard schedule auto-time --repo REPO
enji-guard --project PROJECT schedule auto-time --all-repos
enji-guard schedule auto-time --all-projects

enji-guard email list [REPO]
enji-guard email set REPO [--manual on|off|keep] [--auto on|off|keep]
enji-guard --project PROJECT email set --all-repos [--manual on|off|keep] [--auto on|off|keep]
enji-guard email set --all-projects [--manual on|off|keep] [--auto on|off|keep]
```

Project filtering is a global option:

```text
enji-guard --project NAME_OR_ID status
enji-guard --project NAME_OR_ID audit start OWNER/NAME --all
enji-guard --project NAME_OR_ID email set --all-repos --auto off
```

CLI output is human-readable by default. Use `--json` only when automation needs
the raw machine contract.

## Resolution Rules

- Global `--project` accepts an Enji project id or a case-insensitive project
  name.
- Repo selectors accept an Enji repo id or `owner/name`.
- `project create` takes a plain project name.
- `project rename` and `project delete` accept an exact project selector.
- `project delete` is destructive and requires `--yes`.
- Read commands may omit `--project`; they show all projects or all matching
  repos.
- Write commands may omit `--project` only when the target repo is unambiguous.
- `repo move` uses global `--project` as source project or selector
  disambiguation when needed. `--to-project` selects the destination project.
- Mutating batch commands require explicit scope. Use `REPO` for one repo,
  `--all-repos` with `--project` for every repo in one project, or
  `--all-projects` for every repo in every project.
- `schedule set` and `email set` follow the same write-scope rules.
- Ambiguous targets fail with `BAD_SELECTOR` and include candidates.
- There is no default project.
- No fuzzy matching in write commands.
- `report list [REPO]` is compact inventory; `REPO` is a shortcut for resolving
  the repo and using it as the selector.
- `report read` returns report content. With `--json`, it returns compact
  report metadata by default; use `--full --json` only when the full snapshot
  body is needed.
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
`missing`, `stale`, `connected`, `recon_done`, `active_run_count`, and report
revision drift. `status` must avoid implying freshness when report audits were
generated from different commits: use explicit stale audit names and
`audited=mixed`. Repo list and status payloads include Enji scores by default
as raw `scores`, simple `score_grades`, and a compact `score_summary`; there is
no separate score flag.

`schedule` is the public noun for automatic report audit runs. It exposes domain
settings (`enabled`, `frequency`, time source, timezone) and hides Enji's
`improvement-jobs` payload shape. `schedule set` applies enabled/frequency
changes to all report audits in the selected explicit write scope; it does not
surface manual run time. Enji-assigned run time is the default model, and
`schedule auto-time` resets existing schedules back to that model in the same
explicit write scopes. `schedule timezone` aligns timezone. Per-audit schedule
controls and manual run-time tuning are intentionally not part of the default
workflow. `schedule list` should call out timezone divergence inside one repo
because that often explains stale report audits.
