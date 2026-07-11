# Enji API Field Guide

Observed on 2026-06-28 against the Enji Guard SPA and Fleet backend. This is a
sanitized engineering reference for behavior that is useful when maintaining
the reconstructed contract. The canonical machine-readable API boundary is
[`contracts/enji-openapi.json`](../contracts/enji-openapi.json); when this guide
and the contract disagree, verify the live service and update the contract.

## Surfaces

- `https://guard.enji.ai/guard-app` is the Vite/React SPA.
- `https://fleet.enji.ai` is the backend origin.
- `/api/ux/*` is the frontend-oriented BFF surface and provides most aggregate
  repository operations.
- `/api/v1/*` is the narrower canonical REST surface used for authentication,
  Fleet projects, tasks, runbooks, and GitHub App installations.

## Authentication

Enji currently authenticates automation with an HTTP-only cookie session. No
API-key, PAT, service-account, client-registration, or token-management endpoint
was found in either the SPA or the probed API surface.

`POST /api/v1/auth/refresh` has no body. A successful call rotates the access
and refresh cookies. A client must retain the response `Set-Cookie` values,
deduplicate concurrent refresh attempts, and retry the failed request once. It
must never recursively refresh a failed refresh request. Expiry of the refresh
cookie requires a new OAuth login.

The browser and CLI can invalidate each other's session when both rotate the
same cookie state. The service therefore keeps one writable cookie jar and owns
refresh centrally. Bearer/API-token support remains the preferred long-term
authentication path.

## Audit Identity

One audit can have different identifiers in different API resources. Clients
must expose one canonical alias and keep this translation internal.

| Canonical alias | Report group | Schedule/action key | Legacy improvement kind |
|---|---|---|---|
| `security` | `vulns` | `audit.security` | `vuln-audit` |
| `ai-readiness` | `ai-readiness` | `audit.ai-readiness` | `ai-maturity` |
| `tests` | `tests` | `audit.tests` | `test-audit` |
| `cicd` | `cicd` | `audit.cicd` | not confirmed |
| `config-hygiene` | `config-hygiene` | `audit.config-hygiene` | not confirmed |
| `tech-health` | `tech-health` | `audit.tech-health` | `tech-health` |
| `deps` | `dependency-hygiene` | `audit.dependency-hygiene` | `dependency-hygiene` |
| `cognitive-debt` | `cognitive-debt` | `audit.cognitive-debt` | `cognitive-debt` |
| `dead-code` | `dead-code` | `audit.dead-code` | `dead-code` |
| `recon` | none | `audit.recon` | none |

`GET /api/ux/catalog` is the authoritative discovery source. Its
`metricGroups` array contains published audit axes; `auditAutofixes` contains
autofix variants; and `curatedActions` contains workflow actions. New audit axes
may appear in the catalog before legacy improvement-job kinds exist.

## Repository Lifecycle

Repository onboarding is a client-orchestrated sequence, not one atomic backend
operation:

1. `POST /api/ux/projects/{projectId}/repos` creates membership and returns a
   repository ID.
2. `PUT /api/ux/projects/{projectId}/repos/{repoId}/connection` marks verified
   GitHub App access as connected.
3. `POST /api/ux/repos/{repoId}/audit-runs` with `audit.recon` starts baseline
   diagnostics.

The backend does not implicitly start recon after the first operation. A client
implementing `repo add` must perform the full idempotent sequence. Available
GitHub repositories and access verification are exposed under
`/api/v1/github/app/installations/{installationId}/repos` and `/verify`.

Project creation also spans two resources: create the Fleet project through
`POST /api/v1/projects`, then create its UX projection through
`POST /api/ux/projects`. Fleet deletion is authoritative; deleting only the UX
projection does not reliably remove a project. Product code additionally
forbids deletion of non-empty projects.

Repository transfer uses `transfer/preflight` followed by `transfer`, including
schedule replacements returned or required by preflight.

## Audit Runs And Reports

`POST /api/ux/repos/{repoId}/audit-runs` starts recon or an audit axis. The body
contains `projectId`, `actionKey`, a small `fleetTaskBody`, and a unique
`clientRequestId`. Enji owns the actual runbook selected by the action key.

Progress can be reconciled from three projections:

- repository `active-runs` for the current aggregate view;
- `/api/v1/tasks/{taskId}` and `/activities` for authoritative task-by-ID state;
- repository `audit-rerun-state` for current SHA, last audited SHA, rerun
  eligibility, and the last Fleet task ID.

The report body is returned by
`GET /api/ux/repos/{repoId}/snapshots/upfront.audit.summary?group=<report-group>`
as Markdown in `snapshot.content.report`. Copy and download in the SPA are
client-side actions over this payload; there is no report download endpoint.

`GET /api/ux/repos/{repoId}/audit-history` returns score time series keyed by
report group. Each point contains `when`, `score`, `fleetTaskId`, and the
`bad`/`warn`/`good`/`skip` band counts. Treat score deltas as noisy; reductions
in high-value negative bands are the more useful trend signal.

## Scheduling

There are two schedule families:

- `audit-auto-runs/{actionKey}` is the current SPA path for automatic audit
  reruns and is forward-compatible with catalog-discovered axes.
- `improvement-jobs/{kind}` serves autofix jobs and an older parallel audit
  scheduling surface. Autofix kinds observed are `vuln-fix`, `test-writing`,
  and `dependency-update`.

There is no server-side project batch endpoint. The SPA applies project-wide
settings with a client-side loop over repositories and axes. CLI batch behavior
must therefore throttle requests and retain automatic refresh handling.

An improvement job stores `enabled`, `autoFix`, `autofixVariantKey`,
`frequency`, `daysOfWeek`, `scheduleTime`, `scheduleTimeSource`, `timezone`,
`pentestMode`, and `triedAt`. `frequency` and `daysOfWeek` are independent; the
backend accepts inconsistent combinations and does not normalize them.

`scheduleTimeSource` accepts `auto` or `user`, not `manual`. `timezone` is an
IANA timezone stored on each schedule. `GET /api/ux/schedule-load` requires
`from`, `to`, and `timezone` and returns 30-minute load buckets plus candidate
slots. The SPA selects a low-load candidate and persists the chosen clock time
with source `auto`.

## Email Preferences

Email preferences are addressed by audit action key:

`GET|PUT /api/ux/repos/{repoId}/audits/{actionKey}/email-preferences`

The response contains resolved, override, project, and global layers. PUT is a
partial update despite its method; fields include `manualRunCompletion` and
`scheduledRunCompletion`. The UI checkbox reflects the repository override,
while `resolved` is the effective layered result.

## Additional Domains

The following confirmed surfaces are outside the current CLI/MCP product scope
but belong in the reconstructed contract:

- improvement runs and audit findings, including finding-level autofix results;
- pentest jobs, explicit consent, and one-off pentest runs linked to a project
  web resource;
- project publication and unauthenticated public project dashboards, audit
  history, and snapshots;
- project activity timeline, code-review summary, run language, favorites,
  repository ordering, task links, and task notifications;
- `POST /api/ux/feedback` using multipart form data.

The SPA also uses `/api/ux/fleet-ws` for realtime run updates. OpenAPI does not
describe WebSocket messaging, so this transport remains documented here rather
than represented as an HTTP operation.

## Known Gaps

These endpoints were found but their complete request or response schema was not
captured:

- pentest job updates and pentest consent updates;
- finding-level autofix result writes;
- audit email-watch creation;
- task notifications for in-flight tasks;
- member roles beyond `owner` and `member`;
- the public snapshot identifier format;
- website SEO and user-testing audit surfaces.

Operations with incomplete payloads remain permissive in OpenAPI and carry an
observation marker. Tighten them only after observing the live SPA request or a
successful controlled probe.
