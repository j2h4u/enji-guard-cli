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

## Supply Chain

New Python packages stay in quarantine for 7 to 14 days before normal use, or
they need explicit owner approval for earlier adoption. This is a review and
merge policy, not a separate automated gate.

Lifecycle and install scripts are disabled by default. Only allowlist a script
when it has been reviewed and is required for the change.

Dependabot PRs get the same review standard as any other dependency change.
Reviewers should check the package delta, lockfile updates, install-script
surface, and any Docker or CI pinning changes before merge.

Keep `uv.lock` committed and current. Keep Docker and CI references frozen or
locked to explicit versions or SHAs; update those pins only as part of a
reviewed dependency or maintenance change.

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
