# enji-guard-cli

Python 3.14 CLI and MCP bridge for Enji Guard.

This repository is an early prototype. It supports a shared core, a Typer CLI,
and a FastMCP server that can expose Enji Guard access and compact report
metadata to local tools.

## Requirements

- Python 3.14
- uv
- Docker, for the long-running MCP service

## Install

```bash
uv sync
uv run enji-guard --help
```

## Authentication

Preferred future path is an Enji API token:

```bash
printf '%s' "$ENJI_API_TOKEN" | uv run enji-guard auth import-token --stdin
```

Until API tokens are available, cookie auth is supported as a temporary
compatibility path:

```bash
pbpaste | uv run enji-guard auth import-cookie --stdin --pretty
uv run enji-guard auth status --pretty
```

Do not paste credentials directly into shell history. The auth file defaults to
`~/.config/enji-guard/auth.json` and is written with private file permissions.

## CLI

```bash
uv run enji-guard access --pretty
uv run enji-guard project list --pretty
uv run enji-guard repo current --pretty
uv run enji-guard status --pretty
uv run enji-guard audit start j2h4u/enji-guard-cli --all --pretty
uv run enji-guard wait j2h4u/enji-guard-cli security --pretty
uv run enji-guard report read j2h4u/enji-guard-cli
uv run enji-guard auth refresh --pretty
```

Use the global `--project NAME_OR_ID` filter when a command must be scoped to
one Enji project.

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
`~/.config/enji-guard` writable by the container user:

```bash
mkdir -p ~/.config/enji-guard
chown -R 1000:1000 ~/.config/enji-guard
chmod 700 ~/.config/enji-guard
```

## Development

```bash
just verify
```

The completion gate includes Ruff, basedpyright, import-linter, Vulture,
deptry, OpenAPI contract validation, CRAP <= 30 per function, tests, and Docker
build.

## Security Notes

Cookie auth is temporary and will be removed when API-token support is available
from Enji. MCP HTTP transports do not add their own authentication; keep them
bound to loopback or behind an explicit trusted boundary.

## License

PolyForm Noncommercial License 1.0.0. Commercial use requires separate
permission.
