# ТЗ: Enji Guard Core, CLI и MCP

Документ описывает продуктовую поверхность адаптера. Reconstructed-контракт
нижнего Enji API живет отдельно: `../contracts/enji-openapi.json`.

## Назначение

Инструмент нужен агентам, которые обслуживают репозитории через Enji Guard без
браузерной рутины. Core workflow общий, но CLI и MCP являются разными
проекциями этого workflow. Они не обязаны иметь паритет команд.

Агентские поверхности должны отвечать на рабочие вопросы:

- какие проекты и репозитории доступны;
- подключен ли нужный репозиторий;
- идет ли recon или report-аудит;
- какие отчеты `ready`, `running`, `missing`;
- как запустить recon, report-аудиты, дождаться завершения и прочитать отчет;
- как создавать, переименовывать, удалять проекты и переносить репозитории
  между проектами;
- как настроить расписание и email-уведомления по отчетам.

CLI design зафиксирован отдельно: `cli-surface-design.md`.

## Surface Model

CLI is the advanced agent surface. It may expose reads, writes, project
administration, repository moves, schedule changes, email preferences, auth
bootstrap, and operational commands. CLI should stay task-oriented, but it is
allowed to be broad.

CLI stdout/stderr are command I/O. Application telemetry belongs in the
configured persistent log file, not interleaved with command output.

MCP is the curated read-mostly agent surface. It is for a tech-lead or project
manager agent that needs the Enji picture: project/repo overview, scores,
freshness, active work, report inventory, and report content. MCP should not
mirror CLI or Enji frontend endpoints. Mutating MCP tools are opt-in design
decisions, not default parity work.

Shared code should live in core use cases. Surface registries may reference the
same core capabilities, but each surface chooses its own subset, names, and
parameter shape.

## Архитектура

```text
core        shared use cases and Enji adapter
CLI         advanced agent command wrapper over core
MCP         curated read-mostly tool wrapper over core
```

Правило: core operation должна быть пользовательским сценарием, а не копией
frontend endpoint. CLI и MCP не содержат Enji/auth/business logic.
The private Enji adapter contract module owns endpoint metadata for implemented
lower-level API calls; tests bind it to reconstructed OpenAPI operation IDs and
request body references. OpenAPI must not generate CLI or MCP surfaces directly.

## Основные Нouns

- `project`: inventory Enji projects.
- `repo`: GitHub repo listing, selector resolution, and connection.
- `status`: runtime snapshot across projects/repos/tasks.
- `recon`: preliminary diagnostics, separate from report audits.
- `audit`: report-producing checks.
- `report`: generated report content.
- `wait`: blocking readiness check for all report audits in one repo.
- `schedule`: recurring audit settings.
- `email`: report completion email preferences.

Canonical report audits:

- `security`
- `ai-readiness`
- `tests`
- `tech-health`
- `deps`
- `cognitive-debt`
- `dead-code`

`recon` is intentionally separate and must not be offered as a report audit.

## CLI Surface

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

enji-guard email list [REPO]
enji-guard email set REPO [--manual on|off|keep] [--auto on|off|keep]
enji-guard --project PROJECT email set --all-repos [--manual on|off|keep] [--auto on|off|keep]
enji-guard email set --all-projects [--manual on|off|keep] [--auto on|off|keep]
```

`--project NAME_OR_ID` is a global CLI filter:

```text
enji-guard --project Pets status
enji-guard --project Pets audit start j2h4u/enji-guard-cli --all
enji-guard --project Pets email set --all-repos --auto off
```

Default CLI output is human text/tables. `--json` is the only JSON switch for
automation.

## Resolution Rules

- Project selectors accept exact project id or exact project name.
- Repo selectors accept Enji repo id or GitHub `owner/name`.
- `project create` принимает только имя проекта.
- `project rename` и `project delete` принимают точный project selector.
- `project delete` is destructive and requires `--yes`.
- Read commands can omit project and show all projects/repos.
- Write commands can omit project only when the repo target is unique.
- `repo move` uses global `--project` as source project or selector
  disambiguation when needed. `--to-project` selects the destination project.
- Mutating batch commands require explicit scope. Use `REPO` for one repo,
  `--all-repos` with `--project` for every repo in one project, or
  `--all-projects` for every repo in every project.
- `schedule set` and `email set` follow the same write-scope rules.
- Ambiguous targets return `BAD_SELECTOR` with candidates.
- No default project and no fuzzy matching.
- Repo list/status can sort by `weakest`, `overall`, or `latest-report`; lower
  scores come first, newer report activity comes first.

## Email Preferences

Email preferences are separate from schedules. `manualRunCompletion` controls
mail after manual report runs; `scheduledRunCompletion` controls mail after
scheduled automatic runs. CLI exposes those as `--manual` and `--auto` and
applies them to all report audits for each selected repo.

## Automatic Schedules

`schedule` controls automatic report-audit runs, not raw Enji
`improvement-jobs`. `schedule list [REPO]` shows one table row per repo/report
audit. `schedule set` updates all report audits in the selected explicit write
scope: one `REPO`, `--all-repos` inside `--project`, or `--all-projects`.
Recon is not schedulable here.

## Long-Running Work

Recon and report audits can take tens of minutes. `wait REPO` waits until all
report audits for that repo have results. It exits nonzero on timeout or failed
runs, and reports stale audited commit hashes as context instead of treating
them as failure. `report read REPO` is the main content path after reports
become ready; it reads all currently ready reports unless explicit audit aliases
or `--all` are passed.

`status` must expose scenario state, not raw API internals:

- project and repo identifiers;
- `connected`;
- `recon_done`;
- repo scores: raw `scores`, simple `score_grades`, and `score_summary`;
- active runs;
- repo/report revision: current HEAD, last audited HEAD, out-of-date flag;
- last observed report activity timestamp;
- report states: `ready`, `running`, `missing`;
- summary counts.

## Auth And Runtime

- Docker is the runtime.
- `enji-guard run` starts MCP plus background cookie refresh inside one
  container process tree.
- Cookie auth is temporary. Bearer/API-token support stays first-class and
  should replace cookie auth when Enji provides a token.
- Credentials are stored only in the configured auth file and are never printed.
- The auth file must remain writable because Enji rotates refresh cookies.

Bootstrap cookie auth:

1. Refresh once in the browser.
2. Import a current Fleet `Cookie` header into the auth file.
3. Verify from Docker with `auth refresh`, `auth status`, and logs containing
   `enji_auth_auto_refresh_succeeded`.

## QA Gates

`just verify` is the completion gate. Do not weaken or bypass:

- Ruff;
- type checking;
- import-linter architecture contracts;
- Vulture;
- deptry;
- OpenAPI contract validation;
- CRAP threshold;
- tests;
- Docker build.
