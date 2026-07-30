# enji-guard-cli

Enji Guard audits repositories, but driving it assumes a person in a browser.
A local coding agent cannot start audits, work out which are worth reading, and
act on the findings without someone clicking through a UI.

This is the command surface that closes that gap. Audits start, report, and
read from the terminal, so an agent can run the whole loop unattended: kick off
work across a portfolio, ask what is ready without blocking on what is not,
read findings as text, and queue the fixes worth making. `--json` makes every
answer machine-readable.

Two surfaces, deliberately different in size: the CLI is the full operator
surface, and MCP is a curated read-only view for agents that only need the
picture. It ships as a Docker service so credentials and cookie refresh live in
one place instead of in every agent's environment.

See [ROADMAP.md](ROADMAP.md) for the current product status and remaining
hardening work.

**Running it:** [Runtime](#runtime) · [Authentication](#authentication) ·
[CLI](#cli) · [MCP](#mcp) · [Package installs](#package-installs)
**Understanding it:** [Mental Model](#mental-model) · [Surfaces](#surfaces) ·
[Agent Workflow](#agent-workflow)
**Changing it:** [Development](#development) · [Documentation](#documentation)

## Mental Model

Enji Guard groups repositories into projects. Repository identity is
provider-neutral: selectors use `provider@host:locator` (for example,
`github@github.com:j2h4u/enji-guard-cli` or
`gitlab@gitlab.example:group/subgroup/service`). Add `--project NAME_OR_ID` when the
account has ambiguous repositories or when a batch operation must be scoped to
one project.

An audit is the main unit of repository analysis in this CLI. It has a run
lifecycle, freshness relative to the repository head, scores, and readable
findings. The Enji API may still call some wire payloads reports, but that
transport vocabulary is not part of the user-facing model.

Mutating batch commands require explicit scope. Use `--repo REPO` for one
repository, `--all-repos` with `--project NAME_OR_ID` for every repository in
one project, or `--all-projects` for every repository in every project. Scope is
never implicit: omitting all three exits `1` with `VALIDATION`. The convention
is positional `REPO` wherever a command targets exactly one repository
(`status`, `audit start|read|summary`, `repo *`, `recon start`) and a named
`--repo` on the batch commands (`schedule`, `improvement-jobs`, `email`), where
the repository is one of three competing scope selectors.
`--all-projects` is the only unbounded write scope, so it is confirmed before
it runs: an interactive terminal is prompted, and every non-interactive caller
(agents, MCP, CI, and any `--json` invocation, which never prompts) must pass
`--yes`. Without it the command exits `1` with `CONFIRMATION_REQUIRED` and
changes nothing.
The two irreversible single-target deletions, `project delete` and `repo
remove`, are confirmed the same way and with the same `--yes` flag: a project
cannot be un-deleted, and a detached repository loses its accumulated audit
history, so neither is recoverable by re-running an inverse command.
Mutating commands are designed for agent retries: repeated calls should report
`unchanged`, `already_present`, `already_running`, or equivalent state instead
of duplicating upstream work.

Project admin commands are direct domain actions: create, rename, delete, and
move repositories between projects. `project delete` succeeds only for empty
projects; a project with any repository is rejected by the Portfolio context.

Recon is baseline discovery. Audit runs are separate, slow jobs that produce
readable findings and scores. `status` is the snapshot/readiness/freshness view,
`wait` is the completion check after `status`, `audit summary` is the compact
metadata path, and `audit read` is the content path. `status --json`
separates the latest readable audit artifact from the
current audit task lifecycle, so a stale readable audit and a newly queued or
running task can both be true. Scores are triage hints: use them to sort and
prioritize repositories, then read the audits before changing code. When an
audit exposes commit hashes, compare them with the current checkout before
treating the audit as fresh. Audit reads use report history and the selected
Fleet task id, so prior usable reports remain readable while newer audits run.
CLI `status` and `audit start` do not trust Enji active-runs
alone; the service keeps a short local started-task ledger and reconciles it
with `task-by-id` so incomplete active-runs projections do not trigger duplicate
starts.
`audit read --json` returns the full structured read payload, including each
available Markdown findings body. `audit summary --json` is the compact batch
contract: readable audits include summary metadata, and unavailable audits are
returned with `available: false` plus a reason instead of aborting the whole
batch.

Every audit-aware operation fetches `GET /api/ux/catalog` once for its
invocation. `curatedActions` is authoritative: published audits in the live
response are the available audits, so newly published audits participate
automatically. The catalog is not cached and has no fallback. CLI audit selectors use
the action-key suffix without the `audit.` prefix (for example, `security`
selects `audit.security`). Recon is a separate `audit.recon` action and is not
an audit selector.

The workflow is audit -> findings -> optional improvement. The live catalog's
The provider `auditAutofixes` entries describe available variants. The supported typed
relationships are `security` -> `vuln-fix`, `tests` -> `test-writing`, and
`dependency-hygiene` -> `dependency-update`; pentest is separate. The CLI is
the operator surface for improvement-job management (`list` and `set`), while MCP
remains read-only. Use an explicit `--repo REPO`, `--all-repos` with `--project`, or
`--all-projects` for batch scope. The relationship mapping is temporary and
can be removed when Enji exposes relationships directly.

Audit language is an account-wide preference (`en` or `ru`) shared by all
projects.

CLI output is human text and tables by default. Use `--json` only when another
tool needs structured output.

## Surfaces

Audit and Portfolio own product rules, Application composes their use cases,
and gateway/auth/runtime packages own infrastructure. CLI and MCP stay thin
and call Application instead of duplicating product or backend logic. The
repository treats these DDD-style boundaries as architecture policy, and
`tach` enforces them in the verification gate: `tach.toml` declares the whole
module graph, so an undeclared dependency fails rather than passing unnoticed.

The CLI is the broad operator surface for agents. It exposes reads, writes,
project administration, repository moves, schedule changes, email preferences,
auth bootstrap, and runtime checks.

MCP is the curated read-only surface for agents that need the Enji picture:
portfolio overview across projects and repositories, scores, freshness, active
work, and audit reading for a concrete repository. MCP does not mirror every CLI
command or Enji frontend endpoint, and it does not expose auth bootstrap,
auth-file diagnostics, project administration, or scheduling controls. Auth
belongs to the Docker runtime and CLI operator surface.

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

When working on another repository, pass the fully qualified provider-neutral
selector `provider@host:locator`. GitLab locators preserve nested groups and
require the host. If an agent is already in a GitHub checkout and wants to
derive it from `origin`, it can do that in the host shell and still pass the
qualified selector to the container:

```bash
REPO="github@github.com:$(git config --get remote.origin.url | sed -E 's#^git@github.com:##; s#^https://github.com/##; s#\.git$##')"

docker exec -i enji-guard-cli enji-guard auth status
docker exec -i enji-guard-cli enji-guard repo resolve "$REPO"
docker exec -i enji-guard-cli enji-guard status "$REPO"
```

If the repository is absent from Enji:

```bash
docker exec -i enji-guard-cli enji-guard repo add "$REPO"
docker exec -i enji-guard-cli enji-guard status "$REPO"
```

For a GitLab repository, provide the host and Enji access-credential ID when
adding it:

```bash
docker exec -i enji-guard-cli enji-guard repo add \
  "gitlab@gitlab.example:group/subgroup/service" \
  --repo-access-credential-id "$ENJI_GITLAB_CREDENTIAL_ID"
```

To discover the credential and project metadata first, use the read-only
GitLab group. Credential output contains status and endpoint metadata only;
project output contains a copy-ready repository selector and never prints
clone URLs or secrets:

```bash
docker exec -i enji-guard-cli enji-guard gitlab credentials \
  --scope-type project --scope-owner "$ENJI_PROJECT_ID" --json
docker exec -i enji-guard-cli enji-guard gitlab projects \
  --credential-id "$ENJI_GITLAB_CREDENTIAL_ID" --search service --all-pages
```

Use `--page` and `--per-page` for one project page, or `--all-pages` to follow
the server's `meta.next_page` cursor. Scope filters are always explicit; when
more than one GitLab credential is visible, `gitlab projects` requires
`--credential-id`.

`repo add` is idempotent project membership. If the repository is already
present, continue with the same flow. It starts recon when baseline diagnostics
are not ready. Use `status` to watch progress before expecting audits or
scores.

For triage across all visible repositories:

```bash
docker exec -i enji-guard-cli enji-guard status --sort weakest
```

`status` without a `REPO` returns the compact portfolio overview: project and
repository identity, scores, recon/connection state, and active runs. It does
not fetch every audit status for every repository. Use `status REPO` for the
detailed status of one repository, `audit summary REPO` for compact audit
triage, and `audit read REPO` for audit findings. Add `--json` only when the
result is being consumed programmatically.

For audits:

```bash
docker exec -i enji-guard-cli enji-guard audit start "$REPO" --all
docker exec -i enji-guard-cli enji-guard status "$REPO"
docker exec -i enji-guard-cli enji-guard audit summary "$REPO"
docker exec -i enji-guard-cli enji-guard audit read "$REPO"
```

Recon and audit runs can take tens of minutes. Use `status` first for a
snapshot and stop there when the answer is already clear. Use `wait` only when
you intentionally want to block until completion; do not use short timeouts as
a refresh mechanism. Use `audit summary` for compact triage, and `audit read`
after reports are available. `status` shows stale audits explicitly and uses
`audited=mixed` when audits were generated from different commits. `audit start --json`
returns a `results` matrix, one item per requested audit, with states
such as `started`, `queued`, `already_running`, `up_to_date`, or `failed`.
Prefer reading audits through CLI/MCP instead of relying on email; disable
noisy scheduled mail when it is not part of the workflow.

## Requirements

- Docker
- just
- Python 3.14 for repository development and QA
- uv, only for repository development and QA

## Releases

Package versions come from git tags. GitHub Release `v0.1.0` produces Python
version `0.1.0`; untagged checkouts report a development version derived from
the nearest tag and commit. `enji-guard --version` prints that package version
and the short source commit used for the build; the commit is display-only and
does not alter the package's SemVer.

Release automation is handled by release-please. It opens a release PR from
conventional commits, updates `CHANGELOG.md`, and creates the GitHub Release
after that PR is merged. PR titles must be releasable Conventional Commit
subjects because squash merges use that title as the release input. Use `feat:`
for minor releases, `fix:` for patch fixes, and `!` for breaking major changes;
maintenance work uses `chore:`, `refactor:`, `test:`, `ci:`, `docs:`, `build:`,
or `style:` and still produces a patch release.

Commit-derived notes are sufficient for routine releases. For a broad
user-facing change, curate the implementation PR's release notes when commit
subjects do not tell one coherent story. Explain the current mental model and
the important workflows in plain language; this is a lightweight release
summary, not a command-by-command migration plan. Call out old syntax only
when users of the deterministic CLI/JSON contract would otherwise be surprised.
Release-please remains the only writer of `CHANGELOG.md`; review its generated
release PR as the final user-facing artifact. See
[CONTRIBUTING.md](CONTRIBUTING.md#release-notes).

Use the local release status check after merges and releases:

```bash
just release-status
```

It needs `gh` and Docker buildx available for release checks; those are only
required for this command, not for normal runtime use.
It reports git state, open PRs, the latest GitHub Release, GHCR image
publication, and recent GitHub Actions.

Every candidate image must pass the credentialless runtime contract before it
is published:

```bash
just release-contract enji-guard-cli:local
```

Before merging runtime-sensitive work, exercise the already running authenticated
service through its public CLI and MCP surfaces:

```bash
just release-smoke github@github.com:j2h4u/enji-guard-cli
just release-smoke-recreate github@github.com:j2h4u/enji-guard-cli
just release-smoke-soak github@github.com:j2h4u/enji-guard-cli 300 30 0
```

The first command is read-only. The recreate check proves that authentication
survives container replacement; the bounded soak repeats the same read-only
journey. `release-smoke-mutations` is opt-in and accepts only a unique project
fixture created by that run; do not use it against an existing project.

## Runtime

```bash
mkdir -p ~/.config/enji-guard/logs
chown -R 1000:1000 ~/.config/enji-guard
chmod 700 ~/.config/enji-guard

just docker-up
docker exec -i enji-guard-cli enji-guard --help
docker exec -i enji-guard-cli enji-guard health --ready
```

Compose binds MCP to loopback, defines the service healthcheck, and limits the
container to 512 MiB memory. HTTP MCP transports may bind outside loopback only
with explicit `--allow-external-host`; use that only behind a trusted boundary.
The image default stays loopback-safe; the compose files explicitly bind inside
the container to all interfaces only because they publish the port on host
loopback.

Use `just docker-up` (or `just docker-build`) for local Compose images. It
derives and passes the installed package version and current Git SHA as required
build provenance; a plain `docker compose up --build` intentionally fails until
those values are supplied.

Docker health is full service readiness: local MCP plus cached authenticated
Enji backend readiness. The supervisor alone owns automatic cookie rotation;
gateway requests, `auth status`, readiness, and MCP only observe credentials
and never refresh or replay a request. Repeated auth/backend failures make the
container `unhealthy`, so `docker ps` is a passive dashboard for Enji
connectivity. Bearer/API-token auth is preferred. See
[the auth invariants](docs/decisions.md#supervisor-owned-cookie-session-resilience)
and [deployment recovery](docs/deployment.md#cookie-session-recovery).

For registry-based deployment, use the GHCR image and compose example in
`docs/deployment.md`.

## Package installs

The base wheel is a dependency-light CLI and public read-only Python client;
the MCP server is an explicit, reviewed-and-pinned `mcp[cli]==1.28.1` extra.
Build artifacts locally and install only the surface you need:

```bash
uv build --clear --out-dir dist
python3.14 -m pip install dist/*.whl
enji-guard --help

# Only when running the MCP service outside Docker:
wheel="$(printf '%s\n' dist/*.whl)"
python3.14 -m pip install "${wheel}[mcp]"
enji-guard-service --help
```

The base install intentionally does not install or import MCP. Its public
client is narrow and context-managed: `from enji_guard_cli.client import
EnjiGuardClient`; use `with EnjiGuardClient() as client:` so its pooled
transport is closed deterministically.

Cookie-session recovery remains Docker's long-lived service responsibility.
The supported production path is still the GHCR image because the cookie store
requires one local POSIX host with `flock`, atomic rename, and directory fsync;
NFS/CIFS, multi-host writers, and Windows are unsupported. There is no PyPI
publication path yet: trusted-publishing ownership and the API-key/portability
decisions remain release blockers. The committed artifact contract proves local
wheel build and install only; it does not imply publication readiness.

`enji-guard-service` is the dedicated service entrypoint. The `enji-guard run`
subcommand remains a lazy operator convenience alias.

## Authentication

Bearer/API-token auth is the preferred stable path:

```bash
printf '%s' "$ENJI_API_TOKEN" | docker exec -i enji-guard-cli enji-guard auth import-bearer --stdin
```

Until API tokens are available, cookie auth is supported as a temporary
compatibility path:

Sign in at <https://guard.enji.ai/app/login> so the browser holds a current
session, then trigger an authenticated Fleet request from that tab:

```javascript
await fetch('https://fleet.enji.ai/api/v1/auth/me', { credentials: 'include' });
```

The session is issued by Guard (`https://guard.enji.ai`) and spent against
Fleet (`https://fleet.enji.ai`); the supervisor sends the same Guard origin and
referer when it rotates the cookie.

In DevTools Network, open `GET /api/v1/auth/me` and copy Request Headers ->
Cookie, not response headers. If you inspect the refresh request itself, merge
its response `Set-Cookie` values because that request's `Cookie` header still
contains the old refresh token.

```bash
pbpaste | docker exec -i enji-guard-cli enji-guard auth import-cookie --stdin
docker exec -i enji-guard-cli enji-guard auth status
docker exec -i enji-guard-cli enji-guard health --ready
```

Do not paste credentials directly into shell history. The auth file defaults to
`~/.config/enji-guard/auth.json` and is written with private file permissions.
Keep that directory writable by the container user because Enji rotates refresh
cookies. Credentials belong in the configured auth file, not in checked-in env
templates or persistent `.env` files.

Each import creates a fresh credential revision, even for identical input. The
supervisor uses a private v2 rotation journal before a cookie POST; it is
credential storage and must not be inspected, copied, or deleted. A dispatched
request is never automatically replayed. If status or readiness says that
re-import is required, sign in again at <https://guard.enji.ai/app/login> and
run `auth import-cookie`; that explicit import is the only other credential
writer.

Telemetry at `~/.config/enji-guard/logs/telemetry.jsonl` records non-secret
rotation outcomes with a stable `event_key`. Delivery is at-least-once, so
duplicate records with the same key are possible. The detailed state, storage,
and recovery contract is in [docs/decisions.md](docs/decisions.md); deployment
verification is in [docs/deployment.md](docs/deployment.md#cookie-session-recovery).

After a real cookie re-authentication, refresh the session in the browser,
request `/api/v1/auth/me`, and import the current `Cookie` request header using
the procedure above. The supervisor notices the new credentials immediately,
takes over cookie refresh, and updates backend readiness without a restart.
Validate the running service with:

```bash
docker exec -i enji-guard-cli enji-guard health --ready
```

Treat these commands and the telemetry events as runtime validation, not as a
claim that the current Enji session is valid. If validation remains unhealthy,
check the configured credential-storage ownership and write permissions, then
repeat the browser re-auth/import and validation sequence.

## CLI

```bash
docker exec -i enji-guard-cli enji-guard access
docker exec -i enji-guard-cli enji-guard project list
docker exec -i enji-guard-cli enji-guard project create Pets
docker exec -i enji-guard-cli enji-guard project rename Pets Friends
docker exec -i enji-guard-cli enji-guard project delete Pets --yes
docker exec -i enji-guard-cli enji-guard language show
docker exec -i enji-guard-cli enji-guard language set ru
docker exec -i enji-guard-cli enji-guard repo add github@github.com:j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard repo add gitlab@gitlab.example:group/subgroup/service --repo-access-credential-id "$ENJI_GITLAB_CREDENTIAL_ID"
docker exec -i enji-guard-cli enji-guard repo remove github@github.com:j2h4u/enji-guard-cli --yes
docker exec -i enji-guard-cli enji-guard repo resolve github@github.com:j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard repo move github@github.com:j2h4u/enji-guard-cli --to-project Friends
docker exec -i enji-guard-cli enji-guard status github@github.com:j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard audit start github@github.com:j2h4u/enji-guard-cli --all
docker exec -i enji-guard-cli enji-guard wait github@github.com:j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard audit summary github@github.com:j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard audit summary github@github.com:j2h4u/enji-guard-cli --json
docker exec -i enji-guard-cli enji-guard audit read github@github.com:j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard audit read github@github.com:j2h4u/enji-guard-cli --json
docker exec -i enji-guard-cli enji-guard --project Pets schedule list
docker exec -i enji-guard-cli enji-guard --project Pets schedule set --all-repos --enabled on --frequency workdays --timezone Asia/Almaty
docker exec -i enji-guard-cli enji-guard --project Pets schedule auto-time --all-repos
docker exec -i enji-guard-cli enji-guard improvement-jobs list --repo github@github.com:j2h4u/enji-guard-cli
docker exec -i enji-guard-cli enji-guard improvement-jobs set --repo github@github.com:j2h4u/enji-guard-cli vuln-fix \
  --enabled on --automatic-execution on --frequency workdays --days mon,tue,wed,thu,fri --time auto --timezone Asia/Almaty
docker exec -i enji-guard-cli enji-guard --project Pets email set --all-repos --scheduled off
```

Pass `--json` when a command output is consumed by automation.

### Full Command Surface

Every command the CLI exposes; `enji-guard COMMAND --help` is authoritative for
options.

| Command | Purpose |
|---------|---------|
| `status [REPO]` | Portfolio overview, or one repository audit snapshot when `REPO` is given. |
| `wait REPO` | Block until the repository's audits finish. |
| `health [--ready]` | Process liveness only; `--ready` probes the MCP listener and cached backend readiness. Healthchecks, probes, and CI gates must use `health --ready`, because bare `health` cannot fail while the process runs. |
| `access` | Account plan and limits. |
| `run` | Lazy alias for the optional long-lived MCP service; the dedicated `enji-guard-service` entrypoint is preferred. |
| `auth import-bearer\|import-cookie --stdin`, `auth status` | Credential bootstrap and credential state. |
| `project list\|create\|rename\|delete\|settings` | Project administration; `delete` requires `--yes`. |
| `repo add\|remove\|move\|resolve` | Repository administration; `remove` requires `--yes`. |
| `recon start REPO` | Baseline discovery run. |
| `audit start\|read\|summary REPO` | Run audits, read bodies, read compact metadata. |
| `schedule list\|set\|auto-time\|timezone` | Automatic audit schedules. |
| `improvement-jobs list\|set` | Curated improvement jobs. |
| `email list\|set` | Audit completion email preferences. |
| `language show\|set` | Account-wide audit language. |
| `gitlab credentials\|projects` | GitLab credential and project discovery. |

Each capability has exactly one command. `status` is the only snapshot — with
`REPO` for one repository, without it for the portfolio table — and `wait` is
the only blocking wait. The `audit`, `repo`, and `recon` groups do not repeat
either of them, and there is no `portfolio` group; `auth status` reports
credential state, which is a different subject.

### Exit Codes

The exit status is a stable contract; automation should branch on it instead of
parsing text.

| Code | Meaning | Typical error code |
|------|---------|--------------------|
| `0` | Success. | — |
| `1` | Operator or upstream failure that is not authentication or a missing target. | `VALIDATION`, `CONFIRMATION_REQUIRED`, `ABORTED`, `UPSTREAM`, `STORAGE`, `UNREADY` |
| `2` | Command-line usage error, raised by the parser before anything runs. | — |
| `3` | Credentials are missing, expired, corrupt, or unusable. | `AUTH_*` |
| `4` | The named repository, project, audit, or selector does not exist. | `NOT_FOUND`, `BAD_SELECTOR` |

Errors always go to stderr. Without `--json` they are one `CODE: message` line;
with `--json` they are a `{"code", "message"}` object, so stdout stays either
valid JSON or empty:

```console
$ enji-guard --json status github@github.com:j2h4u/enji-guard-cli
{
  "code": "AUTH_REQUIRED",
  "message": "auth file does not exist. Credential file: ~/.config/enji-guard/auth.json. ..."
}
$ echo $?
3
```

Use the global `--project NAME_OR_ID` filter when a command must be scoped to
one Enji project.
`repo move` uses global `--project` as source project or selector
disambiguation when needed. `--to-project` selects the destination project.

`schedule` controls automatic audit runs, one row per repository and
catalog action key. Its cadence and per-subscription IANA timezone are stored
with each schedule; Enji assigns the run time by default. The service/container
should run with the host timezone. Batch writes are explicit client-side loops:
use `--repo REPO`, `--project NAME_OR_ID --all-repos`, or `--all-projects`.
`schedule set` updates the selected scope, and `schedule auto-time` restores
Enji-assigned run times. Improvement jobs are not audit schedules.

Improvement-job management uses `improvement-jobs` as the canonical resource. Its
operator workflow is list/set per repository or explicit batch scope; it does
not create or replace an audit schedule. Audit schedules remain under
`audit-auto-runs/{actionKey}`. `improvement-jobs list` shows the configured
job scheduler state, including enabled state, automatic-execution state, cadence,
days, time, time source, timezone, and pentest mode when Enji returns it.
`improvement-jobs set` changes only the selected improvement selectors and supports
partial updates with `--enabled`, `--automatic-execution`, `--frequency`, `--days`,
`--time`, and `--timezone`. Use `--time auto` to restore the automatic time
source without changing the existing clock time.

`language show` reports the account preference. `language set en|ru` is
idempotent and changes the account-wide audit language; Enji does not expose
an independent per-project language setter.

## MCP

Local HTTP MCP service:

```bash
just docker-up
```

The current FastMCP streamable-HTTP default endpoint is:

```text
http://127.0.0.1:18082/mcp
```

Override the published loopback port with `ENJI_GUARD_MCP_HOST_PORT`.

The Docker service starts a background cookie refresh loop. Keep
`~/.config/enji-guard` writable by the container user.

## Development

```bash
just verify
```

`just verify` is the completion gate and also builds the image. Release
workflows additionally execute the credentialless runtime contract, while
authenticated live smoke remains an operator gate because CI receives no Enji
credentials.

See [docs/development.md](docs/development.md) for what the gate runs, the
architecture constraints behind it, and the PR and release workflow.
[CONTRIBUTING.md](CONTRIBUTING.md) covers change intake, acceptance criteria,
and handoff rules.

## Security Notes

Cookie auth is temporary and will be removed when API-token support is available
from Enji. MCP HTTP transports do not add their own authentication; keep them
bound to loopback or behind an explicit trusted boundary.
See [SECURITY.md](SECURITY.md) for the supply-chain policy covering package
quarantine, allowlisted lifecycle scripts, Dependabot review, and locked
dependency/Docker/CI references.

## Documentation

- [ROADMAP.md](ROADMAP.md): product status, remaining hardening work, and modular
  install notes.
- [CONTRIBUTING.md](CONTRIBUTING.md): change intake, acceptance, and handoff
  rules.
- [AGENTS.md](AGENTS.md): entry point for coding agents — ops rules and
  pointers to everything else.
- [docs/development.md](docs/development.md): toolchain, architecture
  constraints, product behavior contracts, gates, and the PR/release workflow.
- [docs/decisions.md](docs/decisions.md): current architectural decision index
  and invariants.
- [SECURITY.md](SECURITY.md): credential handling, supported versions, and MCP
  exposure notes.
- [CHANGELOG.md](CHANGELOG.md): release history.
- [docs/deployment.md](docs/deployment.md): GHCR image and production-style
  Docker deployment.
- [docs/enji-api-field-guide.md](docs/enji-api-field-guide.md): sanitized
  reverse-engineering notes, endpoint behavior, and known contract gaps.

## License

PolyForm Noncommercial License 1.0.0. Commercial use requires separate
permission.
