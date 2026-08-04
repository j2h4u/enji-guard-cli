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
  time. `improvement-jobs` owns improvement jobs and is never a scheduling fallback;
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
- **Curated improvement jobs**: the mental model is audit -> findings ->
  optional improvement. The provider `auditAutofixes` field is translated into
  typed catalog improvements, while `improvement-jobs` is the canonical CLI
  operator resource for list/set. Improvement jobs have their own scheduler
  state: enabled state, automatic-execution state, cadence, weekdays, time,
  time source, and timezone are
  read and written through `improvement-jobs`, never through audit `schedule`.
  The temporary typed relationships are `security`/`vuln-fix`,
  `tests`/`test-writing`, and `dependency-hygiene`/`dependency-update`; pentest
  remains separate. MCP stays read-only, and the registry is removable when
  Enji publishes relationships directly.
- **Report language scope**: language is an account-wide `en`/`ru` preference,
  not a project mutation. CLI reads and writes user preferences idempotently;
  it does not expose redundant per-project resolved values.
- **Narrow read-only MCP facade**: MCP stays curated and read-only. MCP
  delivery imports only `McpQueryFacade`, which exposes portfolio overview and
  repository audit reading. Repository audits are compact-first: the default
  DTO is status/summary metadata, and Markdown report bodies require explicit
  audit selectors. CLI and MCP use one deterministic, provider-neutral JSON
  projection with semantic nulls. It does not surface auth bootstrap,
  project/repo writes, scheduling, improvement-job mutation, provider
  extensions, or other operator controls. Stateless MCP describes protocol and
  session handling; it does not prohibit legitimate application state such as
  `FileAuditLedger` or `AuditCatalogObserver` persistence.
- **Base CLI with opt-in MCP service**: the base wheel contains the CLI and
  narrow context-managed public client without importing MCP. The exact v1
  `mcp[cli]==1.28.1` dependency is an optional extra, and the dedicated
  `enji-guard-service` entrypoint owns MCP, background cookie refresh, and
  backend readiness as sibling tasks. Docker installs that extra and owns the
  long-lived cookie recovery lifecycle; standalone CLI requests remain
  observers. Artifact CI validates both wheel modes in clean Python 3.14
  environments, independently from Docker-image QA. This is not PyPI
  readiness: trusted-publishing ownership and the POSIX-only cookie-storage
  portability decision still block publication.
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

  Credential storage remains v2; the refresh journal is v3. Every import
  creates a new opaque revision, including byte-identical data. The journal
  stores one bounded recovery record: source revision, phase, live process
  owner identity, attempt lane and counters, absolute deadline, durable
  not-before time, total dispatch cap, normalized response class, and stop
  reason. A valid v2 terminal journal migrates conservatively. A v2 in-flight
  request has no crash-safe owner evidence, so migration parks it as an
  exhausted unknown outcome and never replays it.

  Before a refresh POST, the coordinator durably records REQUESTED together
  with the current host boot identity, PID, and process start identity. A
  concurrent process observes a live owner and waits for the credential
  revision to change; it does not dispatch. Startup changes REQUESTED to an
  unknown outcome only after proving that owner is no longer alive. This is a
  single-POSIX-host contract based on flock, procfs process identity,
  same-filesystem atomic replace, and fsync; multi-host and network filesystems
  are unsupported.

  Responses are classified only from the public HTTP contract. A complete 200
  successor rotates credentials. Recognized structured envelopes may select a
  bounded validation lane or a terminal rejection. Empty, HTML, malformed, or
  unrecognized 401, 403, 429, and 5xx responses remain ambiguous because status
  alone cannot prove whether the one-time request was consumed. Observer reads
  never clear ambiguity or restore a dispatch budget.

  Recovery is journal-driven rather than a generic retry policy. Each lane has
  a bounded count and durable cooldown, while one absolute deadline and one
  total dispatch cap apply across every process and restart. Calls before the
  not-before time report recovery pending without POSTing. Exhaustion is
  persisted immediately and requires a fresh credential import. While a lane
  remains scheduled, public status reports recovering with
  `reauth_required=false`; a rejected or exhausted generation reports
  `reauth_required=true`. Access authentication remains a separate projection
  and may continue while renewal is degraded.
  These counts, delays, and caps are protocol safety invariants, not deployment
  tuning knobs; they are intentionally absent from environment configuration.

  Ownership here is pinned by `tests/test_side_effect_ownership.py`, not by
  `tach`: `tach` governs imports, and mutating credential state is a call.

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
  remain minimal and never contain credentials, paths, or upstream error
  messages. A separate best-effort refresh observation carries only safe
  diagnostic context: source/successor revisions, remaining access-token
  lifetime, whether the refresh token changed, and bounded upstream request
  identifiers. Failure to emit that diagnostic event never blocks credential
  progress. A failed terminal-outcome delivery remains eligible for later
  reconciliation, and invariant/storage failures remain visible and unready.

  The concise executable invariant reference lives beside the implementation:
  [`state_machine.py`](../src/enji_guard_cli/auth_session/state_machine.py)
  defines legal state evolution,
  [`coordinator.py`](../src/enji_guard_cli/auth_session/coordinator.py) defines
  the side-effect boundary, and
  [`auto_refresh.py`](../src/enji_guard_cli/auth_session/auto_refresh.py) defines
  scheduling and bounded recovery policy. Changes to cookie refresh must keep
  those three contracts and their tests aligned; this decision records the
  rationale rather than a second, competing implementation contract.
- **Supply-chain conservatism**: new Python packages stay quarantined for
  7 to 14 days unless an owner approves earlier adoption; lifecycle and
  install scripts are disabled by default or explicitly allowlisted;
  Dependabot PRs are reviewed like any other dependency change; `uv.lock` and
  Docker/CI references stay frozen or locked to explicit versions or SHAs.
- **OpenAPI as the canonical API boundary**: the reconstructed OpenAPI contract
  is the source of truth for the service API. Markdown docs do not define a
  second contract.
- **Tach as architecture policy**: the tach module graph expresses enforced
  module boundaries, not style preferences.  It replaced import-linter, whose
  contract-by-contract model left any module nobody wrote a contract about
  unconstrained by default; tach declares the graph exhaustively, so an
  undeclared edge is an error. Audit cannot depend on Portfolio.
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
  synchronous `httpx.Client`, which owns the connection pool and is safe to
  share across threads. The blocking call is exposed to asyncio callers through
  `asyncio.to_thread`, because the service supervisor awaits the same client
  from a live event loop that also drives the MCP server and the auth-refresh
  and readiness loops, and that loop must stay responsive. The application
  lifecycle closes the client idempotently after each CLI invocation. Pool
  limits and graceful MCP shutdown timeout are frozen hierarchical settings
  rather than module-level tuning constants.

  This replaced an earlier design in which a single `httpx.AsyncClient` lived on
  a dedicated owner-thread event loop and synchronous callers bridged to it via
  `run_coroutine_threadsafe`. That design was chosen when a synchronous
  collapse would have touched application, delivery, MCP, fanout, and the test
  ports at once. It bought connection reuse across a repository wave, which the
  current design also provides, but it serialized all HTTP protocol work
  through one thread and required roughly 160 lines of self-deadlock avoidance.
  Measured on the batch-read path, the current design is faster at every batch
  size and latency regime tested and equal at the slowest, and both designs
  reuse 100% of their connections on a second wave inside the keepalive window.

  Known trade-off: `asyncio.to_thread` is not cancellable, so cancelling a
  request delivers `CancelledError` to the awaiter promptly while the in-flight
  socket runs to its timeout. Shutdown can therefore be delayed by up to one
  request timeout.
## Credential-neutral gateway boundary

The Enji gateway consumes only `RequestCredentials`: an API base URL, request
headers, and a non-secret credential-kind label. It must not know the durable
cookie schema, refresh journal, auth-file format, or API-key source. Adapters
own those details and project them into the same request model.

The package-first API-key adapter accepts exactly one of `ENJI_GUARD_API_KEY`
and `ENJI_GUARD_API_KEY_FILE`, validates it without logging or representing the
secret, and sends `Authorization: Bearer`. It does not add browser `Origin`
headers and never writes credential state. Consequently API-key CLI calls need
no daemon, and API-key MCP runs without cookie refresh or cached backend
readiness. The stored-cookie adapter remains only until Enji ships keys; the
roadmap cutover still deletes that implementation rather than preserving it as
a fallback. Service health follows ownership: API-key MCP checks its listener,
while temporary cookie mode additionally checks the supervisor-owned cached
backend readiness state.
