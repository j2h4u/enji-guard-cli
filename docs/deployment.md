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

## Runtime configuration map

| Name | Where it is used | Source of truth |
| --- | --- | --- |
| `ENJI_GUARD_IMAGE_REF` | `deploy/docker-compose.ghcr.yml` production image reference | Operator-provided immutable GHCR digest or `sha-<commit>` tag. Do not use `latest`. |
| `ENJI_GUARD_MCP_HOST_PORT` | Local and production compose host port for the MCP HTTP listener | Optional operator override; defaults to `18082`. Container port stays `8000`. |
| `PACKAGE_VERSION` | Local compose build arg embedded in image provenance | Computed by `just docker-build` and `just docker-up`; not hand-written for normal local runtime. |
| `SOURCE_COMMIT` | Local compose build arg embedded in image provenance | Computed by `just docker-build` and `just docker-up`; must be the Git object id behind the image. |
| `~/.config/enji-guard` | Host bind mount for auth state, telemetry, and runtime files | Host-owned service directory, writable by container uid `1000`. |

The common Docker service contract lives in
`deploy/docker-compose.service.yml`. Local development `docker-compose.yml`
adds the build context and local image tag; `deploy/docker-compose.ghcr.yml`
adds the immutable GHCR image reference. Do not duplicate runtime limits,
ports, healthcheck, volumes, or command flags between those entrypoint files.
The image starts the dedicated `enji-guard-service` entrypoint directly; the
compose command supplies only its service options. `enji-guard` remains the
operator CLI used by health checks and `docker exec` commands.

Python and uv setup for GitHub Actions is centralized in
`.github/actions/setup-python-uv/action.yml`. The Dockerfile still pins the
runtime image and builder tool by immutable references, because Docker needs
its own source format. `tests/test_source_policy.py` is the guardrail: it
requires workflows to use the local setup action, verifies that the action and
Dockerfile stay on the same Python/uv versions, and verifies the runtime-only
install mode used by the privileged container publisher.

The remaining values in `src/enji_guard_cli/settings.py` are product constants,
not operator configuration. They are grouped by owner:

| Group | Examples | Owner |
| --- | --- | --- |
| Auth endpoints and rotation timing | Enji base URLs, refresh lead/fallback intervals, revision polling | Product code. Change only with an auth/runtime design change and tests. |
| HTTP transport policy | timeout, retry count, retryable statuses, connection pool limits | Product code. These describe the CLI service contract, not per-host tuning. |
| Readiness and audit polling | backend readiness interval, stale-after window, audit wait defaults | Product code. Expose only after a concrete operator workflow needs it. |
| Local paths and filenames | auth file, telemetry log, readiness state, active-run ledger | Storage contract. Change with migration or compatibility handling. |
| Docker ingress knobs | image reference, MCP host port, local build provenance | Operator/runtime values listed in the table above. |

Keep the auth directory writable by uid `1000`; it contains the credential and
private rotation journal. Docker health uses cached readiness from the
supervisor heartbeat: local MCP must listen, backend readiness must be fresh,
and authenticated Enji checks must not fail repeatedly. Gateway calls,
`auth status`, readiness, and MCP only observe auth; the supervisor is the sole
automatic rotation owner.

The Docker supervisor, not a standalone CLI request, owns long-lived cookie
recovery. The wheel's base install deliberately contains no MCP dependency;
the service requires the reviewed-and-pinned `mcp[cli]==1.28.1` extra. There
is no PyPI upload or release-asset publication in this repository yet. Artifact
CI proves build/install behavior, while trusted-publishing authority plus the
current POSIX-only cookie-storage contract remain publishing blockers.

## Cookie-session recovery

Bearer/API-token auth is preferred. Cookie refresh is a one-time-token flow:
the supervisor records `RESERVED` then owner-bound `REQUESTED` before one POST.
A concurrent process waits while that owner is alive. After a proven abandoned
dispatch, recovery follows only the journal v3 lane, durable cooldown, absolute
deadline, and total dispatch cap; observer requests never restore its budget.
Unstructured or malformed HTTP responses remain ambiguous. Exhausted and
protocol-rejected generations require operator re-import. There is no manual
`auth refresh` command or generic transport retry workflow.

Journal v3 is a local rollback boundary. Older binaries do not understand it;
before rolling back, stop every writer and preserve the auth file, then import a
fresh browser credential under the target version instead of copying or editing
the journal. A valid v2 journal is read conservatively by the current version;
an in-flight v2 request is parked and never treated as replay authorization.

After a real re-authentication, sign in at
<https://guard.enji.ai/app/login> so the browser holds a current session,
request `/api/v1/auth/me`, and import that request's current `Cookie` header.
Do not use `document.cookie`. If using the refresh request itself, merge response
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
by key if consuming them. The best-effort `enji_auth_refresh_observed` event
identifies source and successful successor revisions, the access-token window,
refresh-token change, and upstream request IDs when available. Both event
families deliberately exclude credentials, auth paths, and upstream error
messages. Treat telemetry, `auth status`, and `health --ready` as runtime
verification signals, not proof that an Enji session will remain valid.

The runtime image defaults `/etc/localtime` to UTC, but the provided compose
files bind-mount the host `/etc/localtime` so the running service inherits host
time. Keep that mount intact. Each Enji audit or autofix subscription still
stores its own IANA timezone, such as `Asia/Almaty`, and that per-schedule
timezone remains authoritative for schedule execution.
