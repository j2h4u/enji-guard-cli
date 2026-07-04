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
- `status`: snapshot/readiness/freshness across projects/repos/tasks.
- `wait`: blocking readiness gate for all report audits in one repo.

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
enji-guard project delete PROJECT

enji-guard repo list [--sort default|name|weakest|overall|latest-report]
enji-guard repo resolve REPO
enji-guard repo connect OWNER/NAME
enji-guard repo move REPO --to-project PROJECT

enji-guard status [REPO] [--sort default|name|weakest|overall|latest-report]

enji-guard recon start REPO
enji-guard audit start REPO AUDIT...
enji-guard audit start REPO --all

enji-guard wait REPO

enji-guard report read REPO [AUDIT...] [--all] [--json]

enji-guard schedule list [REPO]
enji-guard schedule set REPO [--enabled on|off] [--frequency FREQ] [--timezone TZ]
enji-guard --project PROJECT schedule set --all-repos [--enabled on|off] [--frequency FREQ] [--timezone TZ]
enji-guard schedule set --all-projects [--enabled on|off] [--frequency FREQ] [--timezone TZ]
enji-guard schedule auto-time REPO
enji-guard --project PROJECT schedule auto-time --all-repos
enji-guard schedule auto-time --all-projects

enji-guard email list [REPO]
enji-guard email set REPO [--manual on|off] [--scheduled on|off]
enji-guard --project PROJECT email set --all-repos [--manual on|off] [--scheduled on|off]
enji-guard email set --all-projects [--manual on|off] [--scheduled on|off]
```

Project filtering is a global option:

```text
enji-guard --project NAME_OR_ID status
enji-guard --project NAME_OR_ID audit start OWNER/NAME --all
enji-guard --project NAME_OR_ID email set --all-repos --scheduled off
```

CLI output is human-readable by default. Use `--json` only when automation
needs the raw machine contract.

## Resolution Rules

- Global `--project` accepts an Enji project id or a case-insensitive project
  name.
- Repo selectors accept an Enji repo id or `owner/name`.
- `project create` takes a plain project name.
- `project rename` and `project delete` accept an exact project selector.
- `project delete` succeeds only for empty projects.
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
- `status` is the report artifact, task lifecycle, and freshness surface.
  `status REPO` shows per-audit report readability separately from queued or
  running audit work.
- `report read` returns report content. With `--json`, it returns the machine
  contract for automation.
- `report read REPO` defaults to all currently ready reports for that repo.
- `report read REPO --all --json` returns one item per known report audit.
  Missing or unreadable reports are explicit `available: false` items instead
  of failing the whole batch.
- `email` preferences apply to all report audits. `--manual` controls mail
  after manual runs; `--scheduled` controls mail after scheduled automatic runs.
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

Public commands should expose scenario state such as report readability,
task lifecycle (`queued`, `running`, `failed`), `stale`, `connected`,
`recon_done`, active work, and report revision drift. `status` must avoid
implying freshness when report audits were generated from different commits:
use explicit stale audit names and `audited=mixed`. Repo list and status
payloads include Enji scores by default as raw `scores`, simple
`score_grades`, and a compact `score_summary`; there is no separate score flag.

`schedule` is the public noun for automatic report audit runs. It exposes domain
settings (`enabled`, `frequency`, timezone) and hides Enji's
`improvement-jobs` payload shape. `schedule set` applies enabled/frequency and
timezone changes to all report audits in the selected explicit write scope; it
does not surface manual run time. Enji-assigned run time is the default model,
and `schedule auto-time` resets existing schedules back to that model in the
same explicit write scopes. `schedule list` shows concrete times with their
source, for example `09:00 (auto)` or `09:00 (manual)`. Per-audit schedule
controls and manual run-time tuning are intentionally not part of the default
workflow. `schedule list` should call out timezone divergence inside one repo
because that often explains stale report audits. Timezone is stored per
schedule, and the service/container should run with the host timezone.
