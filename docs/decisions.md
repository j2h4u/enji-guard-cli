# Decision Index

Current-state reference for the architectural decisions this repository is built
around. This is not a changelog and not a historical record; it exists so future
agents can orient quickly before making changes.

## Decisions

- **Cohesive application inputs**: inline `PLR0913` exceptions are reserved
  for framework-reflected CLI handlers, endpoint-shaped HTTP adapters,
  exact-signature test doubles, and private orchestration seams. A private
  seam is allowed only when its parameters split per-item data from
  per-operation collaborators, no cohesive invariant or value exists, and
  the operation scope is already bound by a closure. Every exception keeps a
  narrowly localized inline `# noqa: PLR0913` that fits one documented
  category; this central rationale avoids repetitive per-site comments.
  Application and domain functions require cohesive typed inputs rather than
  repeated scalar argument lists.

- **Provider-neutral repository identity**: Portfolio identifies a repository
  with `(provider, host, locator)` rather than provider-specific upstream field names.
  Selectors always use `provider@host:locator`; GitLab preserves nested group
  paths. Repository references carry neutral provider ID and web URL fields,
  and idempotency compares the normalized identity tuple. GitLab adds require
  an explicit host and a provider access credential; GitHub adds keep the
  existing App-installation payload.
- **Audit bounded-context vocabulary**: Audit, Portfolio, Application, Auth
  Session, Runtime/Observability, gateway, and delivery have separate
  ownership. An audit is the product-level repository analysis: it owns run
  state, freshness, scores, and readable findings. `report` is reserved for
  Enji/OpenAPI wire contracts, raw upstream translators, and documentation
  explicitly naming external integration vocabulary.
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
- **Two-tier release QA through public surfaces**: credentialless CI starts the
  exact candidate image and validates its hardened Docker, CLI, health, and MCP
  contracts before publication. Authenticated pre-merge smoke and bounded soak
  exercise the running service read-only; optional mutation smoke owns and
  removes only its unique disposable fixture. QA scripts do not import product
  internals, so they validate the same process and protocol boundaries users do.
- **Started-task reconciliation before duplicate audit starts**: `audit start`
  and status reads do not trust upstream active-run projections alone. They
  reconcile those projections with a durable local started-task ledger and
  `task-by-id` lookups so recently started audits are not duplicated while
  upstream state is catching up. `task_id` is the identity boundary: every
  unexpired id-bearing ledger entry is refreshed independently, same-task
  upstream rows are suppressed after reconciliation, and terminal task
  details remove the local guard. Status reduction uses one shared lifecycle
  precedence (`failed`, `completed`, `running`, `queued`) and deterministic
  newest projections when upstream returns duplicates.
- **Temporary cookie auth with first-class API tokens**: cookie auth is a
  compatibility path. Bearer/API-token support is the preferred stable auth
  path and should remain first-class.
- **Supervisor-owned cookie-session resilience**: `enji-guard run` is the sole
  automatic owner of cookie rotation; explicit `auth import-cookie` or
  `auth import-bearer` is the only other credential writer. Gateway requests,
  `auth status`, health/readiness, and MCP are pure observers: they neither
  refresh, replay, nor mutate credentials. Standalone CLI requests have no
  in-request recovery, and there is no manual refresh command.

  Credential storage is v2. Every import creates a new opaque revision,
  including byte-identical data. The private v2 journal has exactly five states:
  `RESERVED` (not dispatched), `REQUESTED` (dispatch began), `ROTATED`
  (replacement captured), `REJECTED` (protocol-confirmed rejection), and
  `OUTCOME_UNKNOWN` (the one-time request may have been consumed). `READY` is
  implicit: a valid credential with no applicable journal. Startup reconciles a
  matching `RESERVED` safely, recovers `ROTATED`, and durably converts an
  abandoned `REQUESTED` to `OUTCOME_UNKNOWN` before ordinary readiness starts.
  `REJECTED` and `OUTCOME_UNKNOWN` are terminal: they remain visible and require
  an operator to import a fresh browser credential, which supersedes the old
  revision and clears its journal. No automatic POST follows `REQUESTED`; a
  failure after dispatch, malformed response, cancellation, timeout, transport
  failure, or 429/5xx is conservatively unknown. Transport retries do not cover
  cookie refresh.

  Storage loads are typed rather than collapsed: `ABSENT`, `CORRUPT`,
  `UNSUPPORTED`, `IO_FAILURE`, clock anomaly, and `LOADED` remain distinct.
  Only `ABSENT` is ordinary `AUTH_REQUIRED`; corrupt, unsupported, journal, and
  I/O states are explicit auth failures. The storage contract is one local POSIX
  host with working `flock`, same-filesystem atomic replace, file and parent
  directory `fsync`. NFS/CIFS and multi-host writers are unsupported. File
  watching is an immediate wake-up optimization; bounded monotonic polling is
  the mandatory fallback because bind-mount events are not guaranteed.
- **Auth resilience observability**: terminal journal outcomes carry a stable,
  non-secret `event_key` and are delivered to telemetry at least once, not
  exactly once; consumers must tolerate duplicates by key. Outcome payloads
  contain stable classifications only, never credentials, paths, or upstream
  error messages. A failed telemetry delivery remains eligible for later
  reconciliation, and invariant/storage failures remain visible and unready.
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
