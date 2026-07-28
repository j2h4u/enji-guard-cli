# Agent Rules

Python 3.14 Docker service exposing Enji Guard through core code, CLI, and MCP.

Most readers here want to **run** the service. `## Ops` below is for them, and
it is the only section that is always relevant. Everything else is a pointer:
read it when the task actually calls for it.

| Read when | Document |
| --- | --- |
| changing the code | [docs/development.md](docs/development.md) |
| changing architecture | [docs/decisions.md](docs/decisions.md) |
| opening a PR | [CONTRIBUTING.md](CONTRIBUTING.md) |
| deploying the image | [docs/deployment.md](docs/deployment.md) |
| using the CLI | [README.md](README.md) |

Three rules do not wait for a pointer:

- `just verify` is the completion gate; never weaken, skip, or suppress a check
  in it.
- Never print secrets. Store credentials only in the configured auth file.
- Run `just release-check` before `gh pr create`.

## Ops

- Docker is the runtime. Verify the running container, not just source.
- Local development compose builds `enji-guard-cli:local`; deployment should
  pull `ghcr.io/j2h4u/enji-guard-cli` with `deploy/docker-compose.ghcr.yml`.
- Recreate the service after runtime, env, image, or auth-mount changes.
- Application telemetry lives in `~/.config/enji-guard/logs/telemetry.jsonl`;
  CLI/MCP agent journey events use the shared telemetry layer. stdout/stderr
  belong to CLI results, progress, and CLI errors.
- The container runs `enji-guard run`: supervisor owns MCP, background cookie
  refresh, and backend readiness heartbeat as sibling tasks. MCP must not own
  refresh.
- Docker health is service readiness: local MCP plus cached authenticated Enji
  backend readiness. Heartbeat records auth/backend failures; it must not call
  refresh directly.
- The host auth file must stay writable because Enji rotates refresh cookies.
- Cookie bootstrap needs exactly one thing from the operator: the cookie.
  Ask for it in a sentence, and hand over a snippet to paste rather than prose
  to follow -- sign in at <https://guard.enji.ai/app/login>, then in that tab's
  DevTools console run

  ```javascript
  await fetch('https://fleet.enji.ai/api/v1/auth/me', { credentials: 'include' });
  ```

  and copy Request Headers -> Cookie from that call in the Network tab.
  The snippet only *creates* a request to copy from: the auth cookies are
  `httpOnly`, so no console one-liner can return them and `document.cookie`
  will not show them. That is why the copy comes from the Network tab, and why
  there is no shorter path to offer.

  Then do the rest yourself: run `import-cookie --stdin`, verify, report. Do
  not hand over a numbered runbook for steps you are able to run. Their
  responsibility ends when the cookie arrives.
  Paste the whole header without pruning it; `AUTH_COOKIE_NAMES` keeps
  `access_token` and `refresh_token` and drops everything else, so
  `cf_clearance` and analytics cookies need no manual editing.
  If using the refresh request itself, merge its response `Set-Cookie` values;
  its request `Cookie` has the old refresh token.
- After bootstrap, prove Docker auth recovery works with `auth status`,
  `health --ready`, and `enji_auth_auto_refresh_succeeded` in logs. MCP tools
  should either work through the configured service auth or return clear auth
  errors.
