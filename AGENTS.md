# Agent Rules

Python 3.14 Docker service exposing Enji Guard through core code, CLI, and MCP.
README.md carries the user-facing CLI model and workflows.
CONTRIBUTING.md carries change intake, acceptance, and handoff rules.

## Development

- Use `uv` only. Keep `uv.lock` current; use hardlink mode outside Docker.
- Keep CLI and MCP thin. Put Enji/auth behavior behind the shared core/API layer.
- Treat import-linter as architecture policy, not style advice.
- Keep runtime tuning in frozen settings dataclasses, not env. Env is for
  credential/security ingress only.
- Keep mutating batch writes explicit; never infer all-project or all-repo scope.
- Keep schedule timezone stored per schedule, run the container with the host
  timezone, and use `schedule auto-time` to restore Enji-assigned run times.
- Surface stale/mixed report freshness explicitly; never hide it behind aggregate status.
- Cookie auth is temporary. Keep bearer/API-token support first-class.
- Never print secrets. Store credentials only in the configured auth file.

## QA

- `just verify` is the completion gate.
- Do not weaken, skip, or suppress Ruff, types, import contracts, Vulture,
  deptry, OpenAPI, CRAP, tests, or Docker build.
- Update reconstructed OpenAPI, docs, and tests together when API behavior changes.

## Ops

- Docker is the runtime. Verify the running container, not just source.
- Local development compose builds `enji-guard-cli:local`; deployment should
  pull `ghcr.io/j2h4u/enji-guard-cli` with `deploy/docker-compose.ghcr.yml`.
- Recreate the service after runtime, env, image, or auth-mount changes.
- Application telemetry lives in `~/.config/enji-guard/logs/telemetry.jsonl`;
  stdout/stderr belong to CLI results, progress, and CLI errors.
- The container runs `enji-guard run`: supervisor owns background cookie
  refresh and MCP as sibling tasks. MCP must not own refresh.
- The host auth file must stay writable because Enji rotates refresh cookies.
- Cookie bootstrap is one-time: refresh in the browser first, then import the
  current cookie state. Prefer a `Cookie` header from any Fleet request made
  after refresh. If using the refresh request itself, merge its response
  `Set-Cookie` values; its request `Cookie` has the old refresh token.
- After bootstrap, prove Docker refresh works: container `auth refresh`,
  CLI/MCP auth smoke, and `enji_auth_auto_refresh_succeeded` in logs.
