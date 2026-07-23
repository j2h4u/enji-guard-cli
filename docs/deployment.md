# Deployment

The development compose file builds `enji-guard-cli:local`. Production-style
deployments should pull the published GHCR image with an immutable reference.

## Image

```text
ghcr.io/j2h4u/enji-guard-cli@sha256:<digest>
ghcr.io/j2h4u/enji-guard-cli:sha-<commit>
```

Images are published after the `CI` workflow succeeds on `main`. Version tags
are also published when a GitHub Release is published, but digest or
`sha-<commit>` refs are preferred for reproducible deployment. Never use
`latest` for deployment.

## Host Layout

```bash
mkdir -p ~/.config/enji-guard/logs
chown -R 1000:1000 ~/.config/enji-guard
chmod 700 ~/.config/enji-guard
```

Copy `deploy/docker-compose.ghcr.yml` to the host deployment directory, for
example `/opt/docker/enji-guard-cli/docker-compose.yml`, choose an immutable
image reference, then run. The compose file declares the stable project name
`enji-guard-cli`, so the commands do not depend on the directory name or a
remembered `-p` flag:

```bash
export ENJI_GUARD_IMAGE_REF=ghcr.io/j2h4u/enji-guard-cli:sha-<git-commit>
# or: export ENJI_GUARD_IMAGE_REF=ghcr.io/j2h4u/enji-guard-cli@sha256:<digest>
docker compose pull
docker compose up -d --remove-orphans --wait
docker exec -i enji-guard-cli enji-guard health --ready
docker exec -i enji-guard-cli enji-guard auth status
```

Keep the auth directory writable by uid `1000`; it contains the credential and
private rotation journal. Docker health uses cached readiness from the
supervisor heartbeat: local MCP must listen, backend readiness must be fresh,
and authenticated Enji checks must not fail repeatedly. Gateway calls,
`auth status`, readiness, and MCP only observe auth; the supervisor is the sole
automatic rotation owner.

## Cookie-session recovery

Bearer/API-token auth is preferred. Cookie refresh is a one-time-token flow:
the supervisor records `RESERVED` then `REQUESTED` before one POST. It never
replays a dispatched request. On restart it reconciles the v2 journal: a safe
reservation is removed, a captured replacement is recovered, and abandoned
dispatch becomes `OUTCOME_UNKNOWN`. `REJECTED` and `OUTCOME_UNKNOWN` require
explicit operator re-import; there is no `auth refresh` command or retry
workflow.

After a real re-authentication, refresh the browser session, request
`/api/v1/auth/me`, and import that request's current `Cookie` header. Do not
use `document.cookie`. If using the refresh request itself, merge response
`Set-Cookie` values because its request header has the old refresh token. The
import creates a new revision, supersedes a terminal generation, wakes the
supervisor promptly, and readiness is re-evaluated without a restart. Validate
the running container explicitly:

```bash
docker exec -i enji-guard-cli enji-guard health --ready
docker exec -i enji-guard-cli enji-guard auth status
```

If readiness remains unhealthy, verify uid `1000` ownership and write
permissions for the whole credential directory, then repeat browser
re-authentication/import and both commands. Keep the storage on one local POSIX
host filesystem: it requires working `flock`, same-filesystem atomic rename,
and file/directory `fsync`; NFS/CIFS and multi-host writers are unsupported.
Watchfiles wakes the supervisor quickly, but bounded polling remains the
fallback for bind mounts that do not deliver events.

Telemetry is JSONL at `~/.config/enji-guard/logs/telemetry.jsonl`. Rotation
events have stable non-secret `event_key` values and are at-least-once: dedupe
by key if consuming them. They deliberately exclude credentials, auth paths,
and upstream error messages. Treat telemetry, `auth status`, and `health
--ready` as runtime verification signals, not proof that an Enji session will
remain valid.

The runtime image defaults `/etc/localtime` to UTC, but the provided compose
files bind-mount the host `/etc/localtime` so the running service inherits host
time. Keep that mount intact. Each Enji audit or autofix subscription still
stores its own IANA timezone, such as `Asia/Almaty`, and that per-schedule
timezone remains authoritative for schedule execution.
