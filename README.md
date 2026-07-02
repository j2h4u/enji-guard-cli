# enji-guard-cli

Python 3.14 CLI and MCP bridge for Enji Guard.

This repository is an early prototype. It supports a shared core, a Typer CLI,
and a FastMCP server that can expose Enji Guard access and compact report
metadata to local tools.

## Mental Model

Enji Guard groups GitHub repositories into projects. Most workflows should use
the GitHub `owner/name` repository selector; add `--project NAME_OR_ID` only
when the account has ambiguous repositories or when a batch operation must be
scoped to one project.

Mutating batch commands require explicit scope. Use a `REPO` argument for one
repository, `--all-repos` with `--project NAME_OR_ID` for every repository in
one project, or `--all-projects` for every repository in every project.

Project admin commands are direct domain actions: create, rename, delete, and
move repositories between projects. `project delete` is destructive and
requires `--yes`.

Recon is baseline discovery. Report audits are separate, slow jobs that produce
readable reports and scores. `status` is the snapshot/readiness/freshness view,
`wait` is the blocking gate, and `report read` is the content path. Scores are
triage hints: use them to sort and prioritize repositories, then read the
reports before changing code. When a report exposes commit hashes, compare them
with the current checkout before treating the report as fresh.
`report read --all --json` is a batch contract: readable reports include
summary metadata, and unavailable reports are returned with `available: false`
plus a reason instead of aborting the whole batch.

CLI output is human text and tables by default. Use `--json` only when another
tool needs structured output.

## Agent Workflow

The service runtime is Docker. Agents should call the CLI inside the running
container instead of installing or running this Python package on the host:

```bash
docker exec -i enji-guard-cli enji-guard --help
```

Application logs are persisted outside the container at:

```text
~/.config/enji-guard/logs/enji-guard.jsonl
```

CLI stdout/stderr are reserved for command results, progress, and CLI errors.
Use the log file for HTTP/auth/runtime telemetry.

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
docker exec -i enji-guard-cli enji-guard repo connect "$REPO"
```

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
`wait` until all report audits have results, and `report read` after reports
are ready. `wait` is a blocking gate; `status` shows stale audits explicitly
and uses `audited=mixed` when report audits were generated from different
commits. Prefer reading reports through CLI/MCP instead of relying on email;
disable noisy scheduled mail when it is not part of the workflow.

## Requirements

- Docker
- just
- uv, only for repository development and QA

## Runtime

```bash
mkdir -p ~/.config/enji-guard/logs
chown -R 1000:1000 ~/.config/enji-guard
chmod 700 ~/.config/enji-guard

docker compose up -d --force-recreate --remove-orphans --wait
docker exec -i enji-guard-cli enji-guard --help
```

Compose binds MCP to loopback, defines the service healthcheck, and limits the
container to 512 MiB memory. HTTP MCP transports may bind outside loopback only
with explicit `--allow-external-host`; use that only behind a trusted boundary.

## Authentication

Preferred future path is an Enji API token:

```bash
printf '%s' "$ENJI_API_TOKEN" | docker exec -i enji-guard-cli enji-guard auth import-token --stdin
```

Until API tokens are available, cookie auth is supported as a temporary
compatibility path:

```bash
pbpaste | docker exec -i enji-guard-cli enji-guard auth import-cookie --stdin
docker exec -i enji-guard-cli enji-guard auth status
```

Do not paste credentials directly into shell history. The auth file defaults to
`~/.config/enji-guard/auth.json` and is written with private file permissions.

## CLI

```bash
docker exec -i enji-guard-cli enji-guard access
docker exec -i enji-guard-cli enji-guard project list
docker exec -i enji-guard-cli enji-guard project create Pets
docker exec -i enji-guard-cli enji-guard project rename Pets Friends
docker exec -i enji-guard-cli enji-guard project delete Pets --yes
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

## License

PolyForm Noncommercial License 1.0.0. Commercial use requires separate
permission.
