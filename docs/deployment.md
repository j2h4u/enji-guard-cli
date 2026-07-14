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
image reference, then run:

```bash
export ENJI_GUARD_IMAGE_REF=ghcr.io/j2h4u/enji-guard-cli:sha-<git-commit>
# or: export ENJI_GUARD_IMAGE_REF=ghcr.io/j2h4u/enji-guard-cli@sha256:<digest>
docker compose pull
docker compose up -d --remove-orphans --wait
docker exec -i enji-guard-cli enji-guard health --ready
docker exec -i enji-guard-cli enji-guard auth status
```

Keep the auth directory writable by uid `1000`; Enji rotates refresh cookies.
Docker health uses cached readiness from the supervisor heartbeat: local MCP
must listen, backend readiness state must be fresh, and authenticated Enji
checks must not fail repeatedly.

Bearer/API-token auth is preferred. For the temporary cookie-session path, the
supervisor owns auto refresh. It keeps a durable, private pending-replacement
journal under the configured credential storage while rotating the auth file;
preserve that storage and its permissions across restarts. The journal is
protected credential-storage state; do not inspect or copy its contents.

After a real re-authentication, refresh the browser session, request
`/api/v1/auth/me`, and import that request's current `Cookie` header. Validate
the running container explicitly:

```bash
docker exec -i enji-guard-cli enji-guard auth refresh
docker exec -i enji-guard-cli enji-guard auth status
docker exec -i enji-guard-cli enji-guard health --ready
```

Use the telemetry JSONL to inspect
`enji_auth_auto_refresh_retry`, `enji_auth_auto_refresh_succeeded`,
`enji_auth_refresh_rotation_deferred`,
`enji_auth_refresh_rotation_recovered`, and
`enji_auth_refresh_rotation_superseded`. These are validation signals, not a
guarantee that the current Enji session is valid. If checks remain unhealthy,
verify uid `1000` ownership and write permissions for credential storage, then
repeat the browser re-auth/import and validation sequence.
