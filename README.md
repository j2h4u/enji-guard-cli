# enji-guard-cli

Python 3.14 CLI and MCP bridge for Enji Guard.

This repository is an early prototype. It supports a shared core, a Typer CLI,
and a FastMCP server that can expose Enji Guard access and compact report
metadata to local tools.

See [ROADMAP.md](ROADMAP.md) for the product status and remaining scope.

## Mental Model

Enji Guard groups GitHub repositories into projects. Most workflows should use
the GitHub `owner/name` repository selector; add `--project NAME_OR_ID` only
when the account has ambiguous repositories or when a batch operation must be
scoped to one project.

Mutating batch commands require explicit scope. Use a `REPO` argument for one
repository, `--all-repos` with `--project NAME_OR_ID` for every repository in
one project, or `--all-projects` for every repository in every project.
Mutating commands are designed for agent retries: repeated calls should report
`unchanged`, `already_present`, `already_running`, or equivalent state instead
of duplicating upstream work.

Project admin commands are direct domain actions: create, rename, delete, and
move repositories between projects. `project delete` succeeds only for empty
projects; a project with any repository is rejected by the core layer.

Recon is baseline discovery. Report audits are separate, slow jobs that produce
readable reports and scores. `status` is the snapshot/readiness/freshness view,
`wait` is the completion check after `status`, and `report read` is the content
path. `status --json` separates the latest readable report artifact from the
current audit task lifecycle, so a stale readable report and a newly queued or
running task can both be true. Scores are triage hints: use them to sort and
prioritize repositories, then read the reports before changing code. When a
report exposes commit hashes, compare them with the current checkout before
treating the report as fresh. Starting a new audit can temporarily hide older
snapshots behind running work, so read any needed snapshots before you start a
fresh audit.
`report read --all --json` is a batch contract: readable reports include
summary metadata, and unavailable reports are returned with `available: false`
plus a reason instead of aborting the whole batch.

CLI output is human text and tables by default. Use `--json` only when another
tool needs structured output.

## Surfaces

Core owns Enji/auth behavior and task-level use cases. The CLI and MCP layers
stay thin and call core instead of duplicating backend logic.

The CLI is the broad operator surface for agents. It exposes reads, writes,
project administration, repository moves, schedule changes, email preferences,
auth bootstrap, and runtime checks.

MCP is the curated read-mostly surface for agents that need the Enji picture:
project and repository overview, scores, freshness, active work, report
inventory, and report content. MCP does not mirror every CLI command or Enji
frontend endpoint.

The reconstructed Enji API contract lives in
`contracts/enji-openapi.json`. Markdown documentation must not become a second
API spec.

## Agent Workflow

The service runtime is Docker. Agents should call the CLI inside the running
container instead of installing or running this Python package on the host:

```bash
docker exec -i enji-guard-cli enji-guard --help
```

Application telemetry is written only to the persistent file outside the
container by default. The current destination is JSONL at:

```text
~/.config/enji-guard/logs/telemetry.jsonl
```

CLI stdout/stderr are reserved for command results, progress, and CLI errors.
Use the telemetry file for HTTP/auth/runtime events and CLI/MCP agent journey
events. It is the minimal foundation for future external sinks and
OpenTelemetry-style export; there is no Prometheus or OpenTelemetry exporter
yet. Each record includes `provenance` (`cli`, `mcp`, `supervisor`, `runtime`,
or `test`) so operational traffic can be separated from test traffic. Tests do
not write to the persistent telemetry file unless they explicitly configure a
temporary log path. Keep stdout/stderr for CLI results, progress, and errors
only.

When working on another repository, pass the repository as `OWNER/NAME`. If an
agent is already in a GitHub checkout and wants to derive it from `origin`, it
can do that in the host shell and still pass an explicit selector to the
container:

```bash
REPO=$(git config --get remote.origin.url | sed -E 's#^git@github.com:##; s#^https://github.com/##; s#\.git$##')

docker exec -i enji-guard-cli enji-guard auth status
docker exec -i enji-guard-cli enji-guard repo resolve "$REPO"
docker exec -i enji-guard-cli enji-guard status "$REPO"
```

If the repository is absent from Enji:

```bash
docker exec -i enji-guard-cli enji-guard repo add "$REPO"
docker exec -i enji-guard-cli enji-guard status "$REPO"
```

`repo add` is idempotent project membership. If the repository is already
present, continue with the same flow. It starts recon when baseline diagnostics
are not ready. Use `status` to watch progress before expecting reports or
scores.

For triage across all visible repositories:

```bash
docker exec -i enji-guard-cli enji-guard status --sort weakest
docker exec -i enji-guard-cli enji-guard repo list --sort latest-report
```

For reports:

```bash
docker exec -i enji-guard-cli enji-guard audit start "$REPO" --all
docker exec -i enji-guard-cli enji-guard wait "$REPO"
docker exec -i enji-guard-cli enji-guard report read "$REPO"
```

Recon and report audits can take tens of minutes. Use `status` for a snapshot,
`wait` as a follow-up completion check, and `report read` after reports are
ready. `status` shows stale audits explicitly and uses `audited=mixed` when
report audits were generated from different commits. `audit start --json`
returns a `results` matrix, one item per requested report audit, with states
such as `started`, `queued`, `already_running`, `up_to_date`, or `failed`.
Prefer reading reports through CLI/MCP instead of relying on email; disable
noisy scheduled mail when it is not part of the workflow.

## Requirements

- Docker
- just
- uv, only for repository development and QA

## Releases

Package versions come from git tags. GitHub Release `v0.1.0` produces Python
version `0.1.0`; untagged checkouts report a development version derived from
the nearest tag and commit.

Release automation is handled by release-please. It opens a release PR from
conventional commits, updates `CHANGELOG.md`, and creates the GitHub Release
after that PR is merged. Use `feat:` and `fix:` only for changes that should be
visible in release notes; keep internal churn under `chore:`, `refactor:`,
`test:`, `ci:`, or `docs:`.

Use the local release status check after merges and releases:

```bash
just release-status
```

It reports git state, open PRs, the latest GitHub Release, GHCR image
publication, and recent GitHub Actions.

## Runtime

```bash
mkdir -p ~/.config/enji-guard/logs
chown -R 1000:1000 ~/.config/enji-guard
chmod 700 ~/.config/enji-guard

docker compose up -d --force-recreate --remove-orphans --wait
docker exec -i enji-guard-cli enji-guard --help
docker exec -i enji-guard-cli enji-guard health --ready
```

Compose binds MCP to loopback, defines the service healthcheck, and limits the
container to 512 MiB memory. HTTP MCP transports may bind outside loopback only
with explicit `--allow-external-host`; use that only behind a trusted boundary.
The image default stays loopback-safe; the compose files explicitly bind inside
the container to all interfaces only because they publish the port on host
loopback.

Docker health is full service readiness: local MCP plus cached authenticated
Enji backend readiness. The supervisor refreshes cookie auth and probes backend
readiness as separate sibling tasks; the probe records failures but does not
perform refresh itself. Repeated auth/backend failures make the container
`unhealthy`, so `docker ps` is a passive dashboard for Enji connectivity.

For registry-based deployment, use the GHCR image and compose example in
`docs/deployment.md`.

## Authentication

Preferred future path is an Enji API token:

```bash
printf '%s' "$ENJI_API_TOKEN" | docker exec -i enji-guard-cli enji-guard auth import-token --stdin
```

Until API tokens are available, cookie auth is supported as a temporary
compatibility path:

```bash
# In the browser, trigger Enji refresh first, then copy a current Fleet
# request Cookie header from DevTools Network.
pbpaste | docker exec -i enji-guard-cli enji-guard auth import-cookie --stdin
docker exec -i enji-guard-cli enji-guard auth status
docker exec -i enji-guard-cli enji-guard health --ready
```

Do not paste credentials directly into shell history. The auth file defaults to
`~/.config/enji-guard/auth.json` and is written with private file permissions.
Keep that directory writable by the container user because Enji rotates refresh
cookies.

## CLI

```bash
docker exec -i enji-guard-cli enji-guard access
docker exec -i enji-guard-cli enji-guard project list
docker exec -i enji-guard-cli enji-guard project create Pets
docker exec -i enji-guard-cli enji-guard project rename Pets Friends
docker exec -i enji-guard-cli enji-guard project delete Pets
docker exec -i enji-guard-cli enji-guard repo add j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard repo remove j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard repo resolve j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard repo move j2h4u/enji-guard-cli --to-project Friends
docker exec -i enji-guard-cli enji-guard status j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard audit start j2h4u/enji-guard-cli --all
docker exec -i enji-guard-cli enji-guard wait j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard report read j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard report read j2h4u/enji-guard-cli --json
docker exec -i enji-guard-cli enji-guard --project Pets schedule list
docker exec -i enji-guard-cli enji-guard --project Pets schedule set --all-repos --enabled on --frequency workdays --timezone Asia/Almaty
docker exec -i enji-guard-cli enji-guard --project Pets schedule auto-time --all-repos
docker exec -i enji-guard-cli enji-guard --project Pets email set --all-repos --scheduled off
docker exec -i enji-guard-cli enji-guard auth refresh
```

Pass `--json` when a command output is consumed by automation.

Use the global `--project NAME_OR_ID` filter when a command must be scoped to
one Enji project.
`repo move` uses global `--project` as source project or selector
disambiguation when needed. `--to-project` selects the destination project.

`schedule` controls automatic report-audit runs. It shows one row per
repo/report audit and can batch update all report audits for one repo or one
explicit batch scope. Use `REPO`, `--project NAME_OR_ID --all-repos`, or
`--all-projects`. Timezone is stored per schedule. The service/container should
run with the host timezone, while Enji assigns the run time by default.
`schedule set` updates enabled state, frequency, and timezone for the selected
scope. `schedule auto-time` resets that scope back to Enji-assigned run times.

## MCP

Local HTTP MCP service:

```bash
docker compose up -d --force-recreate --remove-orphans --wait
```

Endpoint:

```text
http://127.0.0.1:8001/mcp
```

The Docker service starts a background cookie refresh loop. Keep
`~/.config/enji-guard` writable by the container user.

## Development

```bash
just verify
```

The completion gate includes Ruff, basedpyright, import-linter, Vulture,
deptry, OpenAPI contract validation, CRAP <= 30 per function, tests, and Docker
build.

Use `CONTRIBUTING.md` for change intake, acceptance criteria, and handoff rules.

## Security Notes

Cookie auth is temporary and will be removed when API-token support is available
from Enji. MCP HTTP transports do not add their own authentication; keep them
bound to loopback or behind an explicit trusted boundary.

## Documentation

- [ROADMAP.md](ROADMAP.md): product status, remaining MCP scope, and modular
  install notes.
- [CONTRIBUTING.md](CONTRIBUTING.md): change intake, acceptance, and handoff
  rules.
- [AGENTS.md](AGENTS.md): concise development, QA, and ops rules for coding
  agents.
- [SECURITY.md](SECURITY.md): credential handling, supported versions, and MCP
  exposure notes.
- [CHANGELOG.md](CHANGELOG.md): release history.
- [docs/deployment.md](docs/deployment.md): GHCR image and production-style
  Docker deployment.

## License

PolyForm Noncommercial License 1.0.0. Commercial use requires separate
permission.
