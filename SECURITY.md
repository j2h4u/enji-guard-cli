# Security

## Supported Versions

Use the latest published release for normal deployment. The current `main`
branch is acceptable for local development and active prototyping.

## Credentials

Never commit auth files, cookies, bearer tokens, or persistent `.env` files.
Credentials belong in the configured auth file, which defaults to:

```text
~/.config/enji-guard/auth.json
```

Cookie auth is a temporary compatibility path for local authorized use. Prefer
API tokens once Enji provides them.

## MCP Exposure

MCP HTTP transports are unauthenticated by this service. Keep Docker port
bindings on `127.0.0.1` unless you put the service behind your own trusted
network/auth boundary. CLI commands reject external HTTP binds by default; pass
`--allow-external-host` only when that trusted boundary already exists.
The Docker image default is loopback-safe; compose deployments may bind to all
container interfaces only while publishing the host port on `127.0.0.1`.

## Reporting

Open a private issue or contact the maintainer before publishing details of a
credential leak or auth bypass.
