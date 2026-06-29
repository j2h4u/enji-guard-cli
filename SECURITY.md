# Security

## Supported Versions

This project is pre-release. Use the current `main` branch only.

## Credentials

Never commit auth files, cookies, bearer tokens, or `.env` files. Credentials
belong in the configured auth file, which defaults to:

```text
~/.config/enji-guard/auth.json
```

Cookie auth is a temporary compatibility path for local authorized use. Prefer
API tokens once Enji provides them.

## MCP Exposure

MCP HTTP transports are unauthenticated by this service. Keep Docker port
bindings on `127.0.0.1` unless you put the service behind your own trusted
network/auth boundary.

## Reporting

Open a private issue or contact the maintainer before publishing details of a
credential leak or auth bypass.
