# Deployment

The development compose file builds `enji-guard-cli:local`. Production-style
deployments should pull the published GHCR image instead.

## Image

```text
ghcr.io/j2h4u/enji-guard-cli:latest
ghcr.io/j2h4u/enji-guard-cli:v<version>
ghcr.io/j2h4u/enji-guard-cli:<version>
ghcr.io/j2h4u/enji-guard-cli:sha-<commit>
```

Images are published after the `CI` workflow succeeds on `main`. Version tags
are also published when a GitHub Release is published.

## Host Layout

```bash
mkdir -p ~/.config/enji-guard/logs
chown -R 1000:1000 ~/.config/enji-guard
chmod 700 ~/.config/enji-guard
```

Copy `deploy/docker-compose.ghcr.yml` to the host deployment directory, for
example `/opt/docker/enji-guard-cli/docker-compose.yml`, then run:

```bash
docker compose pull
docker compose up -d --remove-orphans --wait
docker exec -i enji-guard-cli enji-guard health --ready
docker exec -i enji-guard-cli enji-guard auth status
```

Keep the auth directory writable by uid `1000`; Enji rotates refresh cookies.
Docker health uses cached readiness from the supervisor heartbeat: local MCP
must listen, backend readiness state must be fresh, and authenticated Enji
checks must not fail repeatedly.
