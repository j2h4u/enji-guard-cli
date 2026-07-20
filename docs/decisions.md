# Decision Index

Current-state reference for the architectural decisions this repository is built
around. This is not a changelog and not a historical record; it exists so future
agents can orient quickly before making changes.

## Decisions

- **Audit bounded-context vocabulary and migration**: the completed refactor
  separates Audit, Portfolio, Application, Auth Session,
  Runtime/Observability, gateway, and delivery ownership. Product language
  uses `audit`; `report` is reserved for Enji/OpenAPI wire
  contracts, raw upstream translators, and documentation explicitly naming
  external integration vocabulary. No compatibility aliases are required.
- **Audit Catalog authority and notification**: every command in the Audit
  Catalog context fetches `GET /api/ux/catalog` once per invocation.
  `curatedActions` is authoritative: published audits in the live response
  define the available audits, and newly published audits participate
  automatically. The local
  `~/.config/enji-guard/state/audit-catalog.json` stores only the previous
  observation for change detection; it is never an API fallback or selector
  source. The first valid catalog establishes a baseline without a business
  notice. Later added, removed, or changed audits produce a text business
  notice on stdout. JSON exposes a stable top-level `audit_catalog` business
  section with `changes`, using an empty array when there are no changes; stderr
  is reserved for errors. CLI selectors use the action-key suffix without the
  `audit.` prefix. Recon remains a separate action and workflow.
- **Audit scheduling identity**: automatic audit schedules use
  `audit-auto-runs/{actionKey}` with the exact catalog action key. Each
  subscription stores its cadence, IANA timezone, and auto or user-selected
  time. `improvement-jobs` is autofix-only and is never a scheduling fallback;
  project-wide operations are explicit client-side batches.
- **Bounded read fan-out**: upstream Enji currently exposes several resources
  only at project, repository, or repository-plus-audit granularity. Independent
  batch reads use the shared order-preserving `BoundedFanout` application
  policy, with concurrency fixed in frozen settings. Selector expansion,
  portfolio status/overview, schedule listing, autofix listing, and email
  preference listing must not create private executors. Mutating batches remain
  explicit sequential loops so idempotency and partial-failure behavior stay
  understandable. A future upstream batch endpoint replaces client fan-out at
  its gateway seam rather than changing domain workflows.
- **Curated autofix management**: the mental model is audit -> findings ->
  optional improvement. `auditAutofixes` is the typed catalog of available
  variants, while `improvement-jobs` is the canonical CLI operator resource
  for list/set. The temporary typed relationships are `security`/`vuln-fix`,
  `tests`/`test-writing`, and `dependency-hygiene`/`dependency-update`;
  pentest remains separate. MCP stays read-only, and the registry is removable
  when Enji publishes relationships directly.
- **Report language scope**: language is an account-wide `en`/`ru` preference,
  not a project mutation. CLI reads and writes user preferences idempotently;
  it does not expose redundant per-project resolved values.
- **Narrow read-only MCP facade**: MCP stays curated and read-only. MCP
  delivery imports only `McpQueryFacade`, which exposes portfolio overview and
  repository audit reading. It does not surface auth bootstrap, project/repo
  writes, scheduling, improvement-job mutation, or other operator controls.
- **Docker-first runtime with a supervisor**: the service runs in Docker and
  `enji-guard run` owns MCP, background cookie refresh, and backend readiness
  as sibling tasks.
- **Started-task reconciliation before duplicate audit starts**: `audit start`
  and status reads do not trust upstream active-run projections alone. They
  reconcile those projections with a durable local started-task ledger and
  `task-by-id` lookups so recently started audits are not duplicated while
  upstream state is catching up.
- **Temporary cookie auth with first-class API tokens**: cookie auth is a
  compatibility path. Bearer/API-token support is the preferred stable auth
  path and should remain first-class.
- **Supervisor-owned cookie-session resilience**: cookie auto refresh belongs to
  the `enji-guard run` supervisor, not MCP. Refresh rotation reserves a durable,
  private pending-replacement journal below configured credential storage so a
  replacement can be recovered after an interrupted auth-file write. The
  journal stores protected recovery state and must be treated as credential
  storage; its secret fields are intentionally not described here.
  HTTP retry profiles allow retries only for reads, safe probes, and idempotent
  mutations; unsafe mutations and auth refresh are not retried by transport.
  Transport delay is exponential with jitter and a 30-second cap. Supervisor
  recovery retries indefinitely with exponential jitter capped by the frozen
  `auto_refresh.retry_max_seconds` setting, and uses the configured re-auth
  interval for `AUTH_REQUIRED` failures.
  Credential-file changes wake the refresh scheduler and backend-readiness loop
  immediately instead of waiting for the next heartbeat. There is no manual
  operator-facing `auth refresh` command; `auth status`, readiness, and
  telemetry are the validation surfaces.
- **Auth resilience observability**: runtime diagnosis uses telemetry events
  `enji_http_retry`, `enji_auth_auto_refresh_scheduled`,
  `enji_auth_auto_refresh_retry`, `enji_auth_auto_refresh_succeeded`,
  `enji_auth_auto_refresh_schedule_failed`,
  `enji_auth_refresh_rotation_deferred`,
  `enji_auth_refresh_rotation_recovered`, and
  `enji_auth_refresh_rotation_superseded`; event payloads contain classification
  and timing, not secret details.
- **Supply-chain conservatism**: new Python packages stay quarantined for
  7 to 14 days unless an owner approves earlier adoption; lifecycle and
  install scripts are disabled by default or explicitly allowlisted;
  Dependabot PRs are reviewed like any other dependency change; `uv.lock` and
  Docker/CI references stay frozen or locked to explicit versions or SHAs.
- **OpenAPI as the canonical API boundary**: the reconstructed OpenAPI contract
  is the source of truth for the service API. Markdown docs do not define a
  second contract.
- **Import-linter as architecture policy**: import-linter expresses enforced
  module boundaries, not style preferences. Audit cannot depend on Portfolio.
  Portfolio cannot depend on Audit except for the explicit typed
  `portfolio.ports -> audit.ports` seam used by recon and status composition;
  new cross-context imports must be moved to application orchestration or an
  intentionally designed shared kernel. Protected ownership contracts reserve
  raw Enji HTTP/wire modules for the gateway and transport for Auth Session and
  the gateway. A protected concurrency contract reserves thread-pool ownership
  for the shared fan-out policy. Contract names state when a rule governs
  direct imports only.
- **Explicit composition root**: dependency construction lives in
  `composition.py`; the application module contains orchestration and typed
  facades, not concrete adapter construction.
- **Auth Session and Runtime/Observability ownership**: Auth Session is
  credential-focused and cannot depend on Audit, Portfolio, application,
  delivery, or raw gateway translators. Runtime/Observability owns supervisor,
  readiness, telemetry, and journey coordination; it may use the narrow MCP
  factory boundary required by the current runtime, but not domain
  implementations or raw gateway HTTP/wire modules.
- **Source ownership policy**: current product layers are checked for imports
  of raw gateway implementations; the anti-corruption boundary remains the
  explicit owner of transport and wire translation.
- **Shared transport lifecycle**: operator gateways share one pooled
  `httpx.AsyncClient` owned by a dedicated event-loop thread. Synchronous
  delivery calls bridge to that loop, and the application lifecycle closes the
  pool idempotently after each CLI invocation. Pool limits and graceful MCP
  shutdown timeout are frozen hierarchical settings rather than module-level
  tuning constants.
