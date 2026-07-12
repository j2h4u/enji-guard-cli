# Decision Index

Current-state reference for the architectural decisions this repository is built
around. This is not a changelog and not a historical record; it exists so future
agents can orient quickly before making changes.

## Decisions

- **Live audit discovery**: every report-aware top-level command fetches
  `GET /api/ux/catalog` once per invocation. There is no cache or fallback;
  `curatedActions` is authoritative, and newly published report actions are
  included automatically. CLI selectors use the action-key suffix without the
  `audit.` prefix. Recon remains a separate action and workflow.
- **Audit scheduling identity**: automatic audit schedules use
  `audit-auto-runs/{actionKey}` with the exact catalog action key. Each
  subscription stores its cadence, IANA timezone, and auto or user-selected
  time. `improvement-jobs` is autofix-only and is never a scheduling fallback;
  project-wide operations are explicit client-side batches.
- **Curated autofix management**: the mental model is audit -> findings ->
  optional improvement. `auditAutofixes` is the typed catalog of available
  variants, while `improvement-jobs` is the canonical CLI operator resource
  for list/set. The temporary typed relationships are `security`/`vuln-fix`,
  `tests`/`test-writing`, and `dependency-hygiene`/`dependency-update`;
  pentest remains separate. MCP stays read-only, and the registry is removable
  when Enji publishes relationships directly.
- **Report language scope**: language is an account-wide `en`/`ru` preference,
  not a project mutation. CLI writes user preferences idempotently and reads
  every project's effective run language after the change.
- **Narrow read-only MCP facade**: MCP stays curated and read-only. It exposes
  portfolio overview and repository report reading, not auth bootstrap,
  project/repo writes, scheduling, or other operator controls.
- **Docker-first runtime with a supervisor**: the service runs in Docker and
  `enji-guard run` owns MCP, background cookie refresh, and backend readiness
  as sibling tasks.
- **Temporary cookie auth with first-class API tokens**: cookie auth is a
  compatibility path. Bearer/API-token support is the preferred stable auth
  path and should remain first-class.
- **Supply-chain conservatism**: new Python packages stay quarantined for
  7 to 14 days unless an owner approves earlier adoption; lifecycle and
  install scripts are disabled by default or explicitly allowlisted;
  Dependabot PRs are reviewed like any other dependency change; `uv.lock` and
  Docker/CI references stay frozen or locked to explicit versions or SHAs.
- **OpenAPI as the canonical API boundary**: the reconstructed OpenAPI contract
  is the source of truth for the service API. Markdown docs do not define a
  second contract.
- **Import-linter as architecture policy**: import-linter expresses enforced
  module boundaries, not style preferences.
