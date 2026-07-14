# Auth Session Resilience — Approved Design

Status: approved design plan; implementation is intentionally out of scope.

## Problem

Enji requests can fail transiently while the cookie-backed session is rotating,
the network is degraded, or Fleet is unavailable. Recovery must improve
availability without replaying unsafe writes, losing a newly issued cookie, or
turning readiness checks into a second authentication/refresh controller.
Bearer/API-token authentication must remain a first-class path and must bypass
cookie recovery entirely.

Tenacity supply-chain approval is user-approved for this design.

## Threat model and invariants

Threats include transient 5xx/timeouts/connection failures, throttling,
concurrent refresh attempts, process/container termination during cookie
rotation, stale or conflicting `Set-Cookie` values, duplicate mutation replay,
credential leakage through logs/telemetry, and false-ready health reporting.

Invariants:

- There is one shared Tenacity-backed HTTP executor. Callers select a named
  retry profile; they do not implement local retry loops.
- `READ` retries only safe reads; `IDEMPOTENT_MUTATION` retries only operations
  whose API contract makes repetition safe; `UNSAFE_MUTATION` never retries or
  replays. `AUTH_REFRESH` is a dedicated bounded profile. `SAFE_PROBE` is
  optional and restricted to explicitly safe readiness probes.
- Retries use bounded exponential backoff with jitter and a maximum delay of
  one hour. A valid `Retry-After` response is honored within the same bound.
- No secret, cookie, bearer token, authorization header, or raw `Set-Cookie`
  value is emitted to telemetry or logs.

## Component boundaries

The HTTP executor owns request execution, profile policy, response/error
classification, `Retry-After`, and backoff. The API/core layer owns endpoint
semantics and declares the profile. CLI and the narrow read-only MCP facade
remain thin and never retry independently.

The supervisor owns Auth Session Resilience: one refresh coordinator, refresh
serialization, the durable protected pending-cookie-rotation journal, and
reconciliation after restart. The cookie store owns atomic protected writes;
the journal records a pre-reservation before making a refresh request and is
cleared only after the resulting cookie state is durably committed. The
readiness heartbeat only observes cached auth/backend state and local task
health; it never refreshes, mutates the journal, or changes readiness by
performing recovery.

Bearer mode is selected before cookie-session logic and executes directly
through the normal executor. It has no cookie journal or refresh side effect.

## Exact failure flow

1. A request enters the single executor with an explicit profile.
2. The executor classifies transport errors, retryable 5xx, and throttling;
   it applies bounded jittered backoff or `Retry-After` only when the profile
   permits it. `UNSAFE_MUTATION` returns the failure immediately.
3. A cookie-auth request receiving an auth/session failure reports a refresh
   signal to the supervisor; it does not refresh inline or replay an unsafe
   operation.
4. The supervisor acquires the refresh lock, rechecks current auth state, and
   pre-reserves a protected durable journal entry containing only the minimum
   non-secret recovery metadata (operation identity, timestamps, state, and
   safe cookie-store version/reference).
5. It performs the `AUTH_REFRESH` flow. New cookie material is merged according
   to the cookie-store rules, written atomically, fsynced as required, and the
   journal is marked committed/cleared only after durable success.
6. On timeout, crash, or ambiguous completion, the journal survives restart.
   Supervisor startup reconciles it against the protected cookie-store state;
   it resumes only the refresh/reconciliation action, never the original
   unsafe write. Duplicate refreshes are serialized and become no-ops when
   current state is already valid.
7. A successful safe read or idempotent mutation may be retried/reissued by
   its profile after confirmed session recovery. An unsafe mutation is
   reported as indeterminate/failed and requires explicit operator handling.
8. Readiness remains observational: it reports cached degraded/auth state until
   the supervisor independently restores service.

## Test plan

- Unit-test profile boundaries, classification, jitter cap, `Retry-After`, and
  the guarantee that unsafe mutations have zero retry/replay attempts.
- Test executor usage to prove there is exactly one HTTP retry path.
- Test serialized refresh, bearer bypass, auth failure signaling, cookie merge,
  atomic journal transitions, crash/restart reconciliation, fsync failure, and
  stale journal handling without exposing secrets.
- Integration-test supervisor, CLI, MCP read-only behavior, and observational
  readiness under expired cookies, concurrent failures, throttling, 5xx,
  network loss, and container restart.
- Assert telemetry/log redaction and verify no original unsafe request is
  automatically replayed after ambiguous refresh.

## Validation and deployment/runtime checks

Implementation must pass `just verify`, including Ruff, type/import contracts,
Vulture, deptry, OpenAPI, CRAP, tests, and Docker build. Before rollout,
inspect the dependency lock and approved Tenacity provenance, then build the
local image and run the service with the configured protected writable auth
mount.

Runtime validation must prove the running container uses the new image/code:
exercise `auth refresh`, CLI auth smoke, cookie rotation success telemetry
without secrets, backend/MCP health, readiness behavior, and restart recovery
with a pending journal. Confirm bearer mode makes no cookie-recovery calls.
Recreate the service after image, runtime, env, or auth-mount changes.

## Rollback

Stop rollout and preserve the journal/auth files for diagnosis. Re-deploy the
last known-good image/configuration, recreating the service, while keeping
credentials protected and writable. Do not delete pending journal entries or
replay unsafe operations during rollback. Verify container health, bearer and
cookie auth paths, readiness observation, and absence of secret telemetry;
then reconcile or retire journal entries only through the approved recovery
procedure.
