# Audit Bounded Contexts Refactor — Design

Status: implemented and verified at the controlled reconstruction snapshot
`530a126`; no deployment was performed as part of this refactor.

## Implementation outcome

Phases 1–6 are implemented in the one-shot controlled reconstruction model:
Audit and Portfolio bounded contexts, the typed application composition seam,
Auth Session, Runtime/Observability, delivery adapters, and final cleanup /
contract hardening are present in the committed tree. The final historical
acceptance snapshot recorded 286 tests, CRAP-gate coverage, Docker build and
runtime checks, and import/source-policy checks. Those numbers describe the
`530a126` verification run; they are not a claim about a later live runtime or
deployment. Optional future work remains explicitly non-blocking: additional
catalog relationships, further gateway endpoint coverage, and follow-up
operator-DX improvements are future items rather than unfinished phases.

## Goals

- Make `audit`, `portfolio`, `repository`, and `auth session` the product
  language in core behavior, models, CLI help, MCP descriptions, and product
  JSON.
- Separate the Audit, Portfolio/Repository, Auth Session, Enji Gateway,
  delivery-adapter, and Runtime/Observability responsibilities without a
  big-bang rewrite.
- Keep Enji/OpenAPI vocabulary at one explicit anti-corruption boundary.
- Preserve the existing hard quality gates, especially import-linter, while
  making the dependency direction express the bounded contexts.
- Keep the CLI broad and operational, and keep MCP curated and read-only.
- Make every migration phase buildable, testable, and revertible.

## Non-goals

- No CQRS, event bus, new framework, generic service/helper layer, or
  speculative plugin architecture.
- No application-code, test, dependency, Docker, runtime, or compatibility
  work in this design-only change.
- No preservation of old product aliases such as `report` commands, report
  domain types, or report-shaped product JSON.
- No change to Enji endpoint semantics, authentication security posture,
  scheduling rules, batch-scope rules, or MCP exposure policy.
- No attempt to make the bounded contexts independently deployable packages.

## Terminology matrix

| Concept | Product vocabulary | Allowed external vocabulary | Allowed locations |
| --- | --- | --- | --- |
| A completed Enji analysis and its findings | `audit` | `report` when naming an Enji artifact | Audit context, CLI/MCP, product JSON; `report` only in gateway translators and explicitly external documentation |
| Markdown/metadata returned by Enji | `audit artifact` / `audit findings` | `report`, `snapshot.content.report` | Gateway wire models/translators and docs labelled “Enji API” |
| A catalog entry | `audit definition` | `curatedActions`, `actionKey` | Audit context; raw catalog translator may retain exact field names |
| A running operation | `audit task` / `audit run` | Fleet task/run fields | Audit context; gateway translator |
| Repository collection and project membership | `portfolio` / `repository` | Enji `project`, `repo` fields | Portfolio/Repository context; exact wire fields at gateway |
| Account credential lifecycle | `auth session` | cookie/token fields | Auth Session context; gateway auth transport boundary |

The unqualified word `report` is a prohibited product identifier after each
phase that migrates its slice. A translator may read `content.report` and
produce `AuditArtifact.body`; it must not leak that field name into a domain
model. Documentation may use `report` only in a heading or sentence that
explicitly identifies Enji/OpenAPI integration vocabulary.

## Context responsibilities and dependency direction

The intended dependency flow is:

```text
CLI adapter ───────┐
                   ├──> application facades/use cases ──> contexts
MCP read-only ─────┘                                  ├──> Enji Gateway
                                                      ├──> Auth Session port
                                                      └──> Runtime/Observability ports
```

The contexts have these responsibilities:

- **Audit** owns catalog interpretation, audit definitions, action selection,
  audit run lifecycle, freshness, findings/artifact reads, waiting, audit
  schedules, and improvement relationships. It owns product types and rules,
  not HTTP or Typer/MCP types.
- **Portfolio/Repository** owns projects, repositories, selectors, explicit
  write scope, membership, moves, inventory, and repository status assembly.
  It may depend on Audit read models/ports for audit status, but does not own
  audit rules.
- **Auth Session** owns credential import/status, cookie rotation, token
  handling, durable auth state, and auth failure classification. It exposes a
  narrow session port to the gateway/runtime and never exposes secrets to
  delivery code or telemetry.
- **Enji Gateway** is the anti-corruption layer. It owns HTTP request specs,
  OpenAPI/wire payloads, endpoint paths, field normalization, upstream error
  translation, and construction of context ports. It is the only place where
  raw Enji names such as `report`, `project`, `curatedActions`, or
  `snapshot.content.report` may be interpreted.
- **CLI delivery** owns Typer commands, argument parsing, text/JSON rendering,
  operator progress, and the broad command surface. It calls application
  facades and never imports gateway internals or context implementation
  modules.
- **MCP delivery** owns FastMCP registration and descriptions. It calls a
  narrow read-only facade built from Audit and Portfolio/Repository use cases;
  it cannot reach mutating use cases, Auth Session controls, or gateway
  internals.
- **Runtime/Observability** owns supervisor orchestration, readiness cache,
  telemetry, journey events, and sinks. It observes context/gateway outcomes;
  it does not become a second domain service or auth-refresh controller.

Application facades are composition seams, not a new generic layer. A facade
may coordinate a concrete use case and dependencies; it must not accumulate
business rules or become a second model.

## External translation boundary

`enji_gateway` will be the only package allowed to know both a context port and
the Enji wire contract. Each endpoint adapter follows this flow:

1. Receive a typed context request or selector.
2. Build the exact OpenAPI request using the existing transport and auth
   session facilities.
3. Validate/normalize the response at the boundary.
4. Translate wire names and shapes into an Audit, Portfolio/Repository, or
   Auth Session result.
5. Return a domain/application payload that contains product vocabulary only.

The existing `enji_api.py`, `enji_api_impl/`, `_enji_api_contract.py`, and
transport modules are migrated behind this boundary rather than rewritten in
one pass. Raw `TypedDict` wire payloads stay in `enji_gateway.wire` (or remain
temporarily in the existing gateway module during a phase). `contracts/
enji-openapi.json` remains canonical for the upstream API. Existing auth
refresh retry profiles and the supervisor-owned resilience behavior remain
unchanged.

## Considered approaches

1. **Big-bang package rewrite**: move every module and rename every public
   field in one commit. It gives a clean tree quickly, but has excessive
   rollback scope, obscures regressions, and conflicts with slow Enji audit
   verification.
2. **Cosmetic directory aliases**: add new context names while retaining all
   old report/core modules and aliases. It reduces short-term breakage but
   leaves two vocabularies and weakens import-linter enforcement.
3. **Incremental strangler migration (selected)**: establish one context seam,
   migrate one vertical slice at a time, delete old names as each slice is
   complete, and tighten contracts after every phase. This keeps each phase
   buildable, makes public breaks deliberate, and fits the approved absence of
   compatibility obligations.

## Phase-by-phase migration

Every phase ends with the existing `just verify` gate, focused tests, and an
import-linter contract update. No phase starts while the preceding phase has
uncommitted migration work.

### Phase 1 — Audit vocabulary seam and catalog vertical slice — completed

This was the first execution wave and was intentionally small. Create the Audit
context seam for catalog behavior only; do not rename the whole report
workflow.

Expected changes in the implementation wave:

- Create `src/enji_guard_cli/audit/__init__.py`,
  `src/enji_guard_cli/audit/catalog.py`, and
  `src/enji_guard_cli/audit/models.py`.
- Move the product-facing `AuditCatalog` and `AuditDefinition` definitions
  from `audits.py` into the new Audit package, then delete `audits.py` after
  all imports are migrated; no compatibility re-export is added.
- Move catalog parsing currently in `core_impl/catalog.py` to
  `audit/catalog.py` and keep the existing `curatedActions` interpretation
  behind the gateway-facing input boundary.
- Keep catalog snapshot/change observation in the gateway boundary for this
  phase, but return Audit-owned change types from its public seam.
- Update `core.py`, `core_impl/autofixes.py`, `core_impl/audit_runs.py`, CLI
  catalog wiring, and their focused tests to import the new Audit seam.
- Add `audit` package entries to the import-linter contract and prohibit Audit
  from importing CLI/MCP, HTTP, Typer, MCP, transport internals, and raw
  gateway contract metadata.
- Do not rename CLI commands, report read models, JSON keys, auth, runtime,
  or the existing audit workflow in this phase.

Phase 1 acceptance is: catalog-driven selectors and autofix catalog behavior
are unchanged at the boundary; all product types used by catalog code live in
`audit`; no old `audits` import remains; and `just verify` passes.

Phase 1 status: completed. The acceptance criteria are met: catalog-driven
selectors and autofix catalog behavior are unchanged at the boundary, all
product types used by catalog code live in `audit`, no old `audits` import
remains, and the historical `530a126` verification passed `just verify`.

### Phase 2 — Gateway facade and Audit run lifecycle — completed

Expected modules to create or rename:

- Create `src/enji_guard_cli/enji_gateway/__init__.py`, `ports.py`,
  `wire.py`, and `audit_gateway.py`.
- Move endpoint behavior from `enji_api.py` and relevant pieces of
  `enji_api_impl/` behind `AuditGateway` and `PortfolioGateway` ports; move
  only raw request/response structures to `enji_gateway/wire.py`.
- Create `audit/runs.py`, `audit/tasks.py`, and `audit/artifacts.py` from the
  corresponding `core_impl/audit_runs.py`, `audit_tasks.py`,
  `report_reads.py`, `report_wait.py`, `report_workflows.py`, and the audit
  portions of `repo_status.py`.
- Translate `snapshot.content.report` to an Audit artifact at the gateway;
  the Audit context must expose `audit`, `artifact`, and `findings` names.
- Update CLI/MCP facades to use the application-facing Audit operations.

The phase keeps the existing public CLI until the breaking surface phase, so
the implementation can prove behavior before changing command names.

Phase 2 status: completed at `530a126`; typed gateway ports, Audit lifecycle,
task construction, and artifact translation are implemented.

### Phase 3 — Portfolio/Repository context — completed

Create `src/enji_guard_cli/portfolio/__init__.py`, `projects.py`,
`repositories.py`, `selectors.py`, and `status.py`. Move project administration,
target resolution, write-scope validation, repo status/inventory, and repo
membership from `core_impl/project_admin.py`, `targets.py`, `selectors.py`,
`status_views.py`, and the relevant parts of `repo_status.py`. The context
consumes Audit status summaries through an explicit port and does not import
Audit implementation modules. Migrate `core.py` composition and the portfolio
MCP operation first; then CLI project/repo commands.

Phase 3 status: completed at `530a126`; Portfolio owns selectors, explicit
mutation scope, membership operations, and repository status assembly.

### Phase 4 — Auth Session and Runtime/Observability seams — completed

Create `src/enji_guard_cli/auth_session/__init__.py`, `service.py`, and
`ports.py`; move the public auth behavior from `auth.py` and the auth
implementation from `auth_impl/` behind the new seam. Create
`src/enji_guard_cli/runtime_observability/` with `readiness.py`,
`telemetry.py`, and `journey.py` only if the move improves ownership; otherwise
retain the files and enforce the package boundary in place. Runtime keeps
supervisor ownership of refresh, readiness remains observational, and no
secret-bearing types cross into delivery.

Phase 4 status: completed at `530a126`; Auth Session and Runtime/Observability
boundaries are implemented with supervisor-owned refresh and readiness.

### Phase 5 — Delivery adapters and public vocabulary break — completed

Create `src/enji_guard_cli/delivery/cli/` and `src/enji_guard_cli/delivery/mcp/`
as thin adapter packages. Move `cli_impl/` and `mcp_server.py` incrementally,
then delete the old locations when imports are gone. Rename the product
commands and payloads described below, update README, field guide, CLI help,
surface-contract tests, and MCP descriptions together. Remove all temporary
report-named domain modules and aliases.

Phase 5 status: completed at `530a126`; delivery adapters use the application
surface and the product vocabulary break has no compatibility aliases.

### Phase 6 — Cleanup and contract hardening — completed

Delete empty compatibility seams, consolidate only genuinely duplicated
context code, and make the final import-linter contracts reflect the context
graph. Re-run current-tree source-policy checks for forbidden product imports
of raw gateway implementations, then
perform full CLI/MCP and Docker runtime verification in the implementation
workstream. This design artifact itself does not perform those checks.

Phase 6 status: completed at `530a126`; import contracts and current-tree
source ownership checks are hard gates. The recorded full verification is historical, with no deploy
or live-runtime claim from this document.

## Expected public breaking changes

These changes happen only in Phase 5, not during this design task:

- `report read` becomes `audit read`; `report summary` becomes `audit summary`.
  `audit start` and recon remain under the `audit` command group.
- CLI selector/help language changes from “report audit” to “audit”; no
  `report` command alias is retained.
- Product JSON keys change from report terminology to audit terminology, for
  example `reports` to `audits`, `report_status` to `audit_status`, and
  report-artifact fields to audit-artifact/findings fields. The exact final
  schema must be versioned in the tests and documented in the same phase.
- MCP tool descriptions and returned product payloads use audit terminology;
  tool names change only where they currently expose product report language,
  with no mutating tools added.
- Internal Python imports and public facade symbols such as
  `read_reports_for_repo`, `ReportStatusPayload`, and `ReportWaitPayload` are
  renamed to Audit equivalents and old symbols are removed.

The following remain exact external integration vocabulary where required:
`curatedActions`, `auditKey`/`actionKey`, `snapshot.content.report`, endpoint
paths, and other OpenAPI field names. They must not be “cleaned up” in the
wire contract merely to satisfy product naming.

## SOLID and DRY guardrails

- Single responsibility: a context owns rules; a gateway owns translation; a
  delivery adapter owns presentation; runtime owns orchestration/observation.
- Open/closed: new catalog audits come from authoritative catalog data and do
  not require a new branch or command registration.
- Liskov: ports represent the existing request/result guarantees; fake test
  gateways must be substitutable without special production-only behavior.
- Interface segregation: Audit, Portfolio/Repository, Auth Session, and
  read-only MCP ports expose only the operations their callers need.
- Dependency inversion: contexts depend on small typed ports; composition in
  the application facade binds them to Enji Gateway and Auth Session.
- DRY means one implementation of selector validation, catalog interpretation,
  audit status/freshness, auth refresh policy, and rendering primitives. Do
  not extract a generic helper solely to make two unrelated contexts look
  similar.
- Frozen settings dataclasses remain settings; environment variables remain
  credential/security ingress only. No context may print secrets.

## Import-linter contract evolution

The existing contracts remain hard gates throughout. In successive phases:

1. Add `audit`, `portfolio`, `auth_session`, `enji_gateway`, and eventual
   delivery/runtime packages to the layer graph before moving imports.
2. Replace broad `core_impl` exceptions with explicit contracts stating that
   Audit and Portfolio/Repository cannot import delivery/frameworks, HTTP,
   transport internals, or raw gateway wire modules.
3. Make `enji_gateway` the only context-facing adapter that may import
   `_enji_api_contract`, `transport`, and the HTTP client; Auth Session may
   use its own approved auth/transport port but not context or delivery code.
4. Keep `mcp_server` forbidden from broad/mutating facades and direct gateway,
   Auth Session, runtime-control, and CLI imports.
5. Keep CLI forbidden from gateway internals and keep all framework imports in
   delivery adapters.
6. Add a source-policy test that rejects product-layer imports of raw gateway
   implementations, including imports routed through the gateway package root.

## Test and documentation plan

Tests move with the owning module and keep behavior-focused names. Add unit
tests for Audit catalog parsing, action selection, artifact translation, and
freshness; Portfolio selector/scope and status assembly; Auth Session
classification and redaction; and Gateway request/response translation.
Retain integration tests for exact OpenAPI paths and raw fields in the Gateway
test module. Update CLI surface and JSON contract tests for the breaking names,
MCP read-only/surface tests, import-linter/source-policy tests, and the full
`just verify` suite after every phase.

Update `README.md`, `docs/enji-api-field-guide.md`, `CONTRIBUTING.md` only when
the implementation phase changes their stated behavior. Keep external Enji
field references explicitly labelled as wire vocabulary. Update
`docs/decisions.md` only for durable architectural decisions; keep migration
detail here.

The observed `email list --json` no-output defect is a separate small DX fix.
It should receive its own focused CLI regression test and minimal command-path
fix in a separately scoped change. It must not be hidden in a context move,
used as evidence for this refactor, or bundled into Phase 1.

## Risks and rollback

| Risk | Mitigation | Phase rollback |
| --- | --- | --- |
| Product/wire vocabulary leaks or accidental JSON drift | Boundary translators, current-tree ownership checks, golden JSON tests | Revert the phase commit; do not add aliases |
| Duplicate business rules during migration | One owner per moved function; delete old implementation immediately after imports move | Restore the previous facade and remove the new seam |
| Import cycles | Add contracts before moves; keep composition in facades | Revert only the phase’s move and contract change |
| Audit freshness/task behavior changes | Compare old/new payload fixtures and slow-job lifecycle tests before CLI rename | Keep old command surface for the prior completed phase |
| MCP accidentally gains operator controls | Explicit read-only facade and surface tests | Revert MCP adapter move; no runtime/auth rollback needed |
| Auth/runtime regression | Do not alter resilience policy in context moves; run auth/runtime tests and container verification in implementation | Redeploy last known-good image/config and preserve protected auth state |

Rollback is phase-granular. A failed phase is reverted as a unit, with no
database or credential migration implied by this refactor. A public vocabulary
break is not rolled back by retaining aliases; if rollback is required, restore
the last complete pre-break release and its documentation.

## Full acceptance criteria

- All product-facing domain and delivery language uses `audit`, not `report`.
- Product layers do not import raw gateway HTTP, transport, contract, client,
  or wire implementations; external field translation remains gateway-owned.
- Audit, Portfolio/Repository, Auth Session, Enji Gateway, CLI/MCP delivery,
  and Runtime/Observability responsibilities and dependency direction are
  represented by code boundaries, not only comments.
- No context imports Typer, MCP, HTTP clients, transport internals, or another
  context’s implementation; MCP remains curated and read-only.
- Catalog authority, explicit batch scope, idempotency, audit scheduling,
  freshness visibility, auth resilience, telemetry redaction, and Docker
  runtime ownership remain behaviorally intact.
- Each phase passes `just verify`; the final implementation passes exact
  OpenAPI, import-linter, source-policy, CLI/MCP surface, unit/integration,
  and runtime-container verification.
- The final public CLI/MCP/JSON break is documented and tested with no
  compatibility aliases.
- `email list --json` has a separately tracked and tested DX fix; its absence
  does not block or get conflated with the bounded-context refactor.
- This plan’s Phase 1 scope is completed before any later phase begins.
