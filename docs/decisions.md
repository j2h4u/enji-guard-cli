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
  for list/set. Autofix jobs have their own scheduler state: enabled state,
  automatic-fix state, cadence, weekdays, time, time source, and timezone are
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
  `REJECTED` is terminal for refresh automation: it remains visible and requires
  an operator to import a fresh browser credential, which supersedes the old
  revision and clears its journal. Terminal refresh state is not local logout:
  gateway requests, `auth status`, health/readiness, and MCP may continue using
  the stored access cookie while `GET /api/v1/auth/me` still accepts it. Public
  status surfaces that as authenticated access plus a degraded `refresh_state`
  and `reauth_required`, so operators see the upcoming browser-import need
  without the CLI pretending the session is already unusable. No automatic POST
  follows `REQUESTED`; a
  failure after dispatch, malformed response, cancellation, timeout, transport
  failure, or 429/5xx is conservatively unknown. Transport retries do not cover
  cookie refresh.

  `OUTCOME_UNKNOWN` is not terminal but unadjudicated, and **the refresh loop
  is what adjudicates it**. Parked on an ambiguous generation, the loop asks the
  backend what the refresh actually did: a `GET /api/v1/auth/me` carrying the
  credential already on disk. The refresh token is not withheld — it rides the
  stored `Cookie` header like every other cookie. What makes the probe safe is
  the *endpoint*: a read that does not consume refresh tokens.

  Deciding whether to clear the renewal journal still belongs inside the loop.
  An earlier design put adjudication in the readiness probe, which both broke
  the observer rule above and silently did nothing: clearing the journal does
  not change the credential revision, the loop waits on that revision, and the
  credential watcher is filtered to the auth file alone. Readiness may report
  ready while renewal is degraded, but it must not mutate credential state or
  pretend that readiness cleared the refresh loop's parked generation. The loop
  needs no waking, because the task that must act is the one deciding.

  A `401`/`403` returned by `POST /api/v1/auth/refresh` is not enough, by
  itself, to prove refresh-token rejection. The CLI talks to a public endpoint
  behind reverse proxies and deployment machinery, so a hop-level, HTML, empty,
  malformed, or otherwise proxy-shaped auth failure is ambiguous after
  dispatch. Such responses become `OUTCOME_UNKNOWN` and use the same bounded
  adjudication path as timeouts and 5xx. `REJECTED` is reserved for an
  Enji-protocol rejection, currently the authenticated JSON envelope carrying
  `AUTH_INVALID`; that parks the refresh loop for an import and must not be
  retried automatically. Adjudication is never attempted with a credential that
  is not still usable, and readiness remains observer-only: it checks ordinary
  access and surfaces renewal degradation, but it does not adjudicate or
  refresh.

  **A `200` is weaker evidence than it looks, and the design depends on knowing
  that.** `/api/v1/auth/me` authenticates the `access_token` JWT, which stays
  valid until its own expiry whether or not the refresh token was consumed — and
  refresh is scheduled `auto_refresh.lead_seconds` (300s) *before* that expiry,
  so the probe runs while the old JWT is still good in both worlds. `200`
  therefore means "the access token still works", not "the rotation never
  landed". No endpoint can prove the latter; only spending the refresh token
  can.

  So clearing the journal on `200` is not proof, it is a bounded bet. If the
  rotation did land, the cleared journal lets the next scheduled refresh re-send
  a consumed token — the very replay `REQUESTED` exists to prevent, deferred by
  one hop. That is accepted here because the downside is already spent: if the
  rotation landed and its successor was lost, the session is unrecoverable
  regardless, so a rejected replay costs nothing that was not already gone. The
  bet pays in the common case — a gateway `502` during a backend redeploy never
  reached the app, so nothing rotated and the service heals itself.

  Clearing enqueues an `adjudicated_alive` outbox record beside the standing
  `outcome_unknown` one. The failure record is deliberately not retracted: the
  ambiguity really happened. Without the resolution record telemetry would show
  a rotation that failed and never show it recovering, which reads as an outage
  nobody fixed. `adjudicated_alive` is an outbox-only outcome — no journal state
  carries it, so a journal claiming it is corrupt.

  This rests on one backend coupling, pinned by tests for incident recovery and
  dead-source behavior: a consumed refresh token must eventually draw the
  Enji-protocol `AUTH_INVALID` rejection, which lands in `REJECTED` and asks for
  an import exactly once. Proxy-shaped 401/403 responses do not qualify. If the
  backend instead answered only ambiguous failures for a consumed token,
  adjudication could oscillate (clear → refresh → `OUTCOME_UNKNOWN` → probe
  `200` → clear) until the access-token deadline closes.

  **Adjudication is therefore bounded by the access token's own expiry.** Past
  it the probe can only return `401` whether or not the rotation landed, so
  continuing would invent a verdict and probe forever. The window closes, the
  loop stays parked, and a human is asked once. The oscillation cannot outlive
  that deadline, and it costs no state beyond the credential itself: the
  deadline is data already in the credential, read by
  `cookie_access_expires_at`. An expiry that cannot be read is not a window
  that can be proven open, so it closes too.

  That deadline makes timer placement part of the invariant. The refresh loop
  must attempt adjudication immediately after it parks on `OUTCOME_UNKNOWN`,
  before any polling sleep, and the polling interval must be shorter than the
  refresh lead window. Otherwise a refresh attempted `lead_seconds` before JWT
  expiry can sleep away the entire evidence window and ask for a browser import
  even when `/api/v1/auth/me` would have shown that the held credential was
  still usable. A successful `200` adjudication also waits one bounded
  adjudication polling interval before redispatching refresh, unless a
  credential revision change wakes it first. That keeps backend-recovery probes
  timely without turning a still-unhealthy refresh endpoint into a tight
  `POST /auth/refresh` → `GET /auth/me` loop.

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
