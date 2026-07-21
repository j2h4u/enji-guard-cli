# Enji API Field Guide

Observed on 2026-06-28 against the Enji Guard SPA and Fleet backend, then
re-checked against the public SPA bundle and this branch on 2026-07-21. This is
a sanitized engineering reference for behavior that is useful when maintaining
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

### Bundle Evidence And Classification

The 2026-07-21 reconciliation used the public `guard-app` index last modified
2026-07-16 and all 53 JavaScript chunks referenced by
`index-Crr0a1l2.js`. No authenticated endpoint was called. The main evidence
chunks are `app-core-M6Hsa8zn.js`, `github-app-BQBaMpt8.js`,
`repo-provider-icon-DSU2kwin.js`, `star-button-BvtzxAJl.js`,
`fleet-aware-schedule-time-ZojCwCV3.js`, `audit-report-archive-DgvcmYa7.js`,
`executive-summaries-B3Vq6tIK.js`, `use-audit-run-CoB5D8ZT.js`,
`settings-BceUw6GW.js`, and `admin-COdxBH64.js`.

In this guide, **confirmed current** means the current bundle constructs the
request or reads the named response field. **Contract gap** means that observed
operation is absent from `contracts/enji-openapi.json`. **Uncertain** means
static code proves the client shape but not that the deployed backend accepts
it, or the bundle forwards an object whose complete schema cannot be recovered
from the request helper alone.

## Authentication

Enji currently authenticates automation with an HTTP-only cookie session. No
API-key, PAT, service-account, client-registration, or token-management endpoint
was found in either the SPA or the probed API surface.

`POST /api/v1/auth/refresh` has no body. A successful call rotates the access
and refresh cookies. A client must retain the response `Set-Cookie` values,
deduplicate concurrent refresh attempts, and retry the failed request once. It
must never recursively refresh a failed refresh request. Expiry of the refresh
cookie requires a new OAuth login.

The current `app-core-M6Hsa8zn.js` auth helpers also use:

- `GET /api/v1/auth/me`, reading `user_id`, `email`, `name`, and `role`;
- browser navigation to `GET /api/v1/auth/login?redirect=<url>`;
- `POST /api/v1/auth/activate` with `{"code": "..."}`;
- `POST /api/v1/auth/logout` with no body.

Only `auth/me` and `auth/refresh` are reconstructed in OpenAPI. Login,
activation, and logout are confirmed-current contract gaps. Their success
response schemas remain uncertain because the SPA does not consume a body.

The service keeps one writable cookie jar and owns refresh centrally. The
documented operator surface is `auth import-cookie`, `auth import-bearer`, and
`auth status`; there is no manual `auth refresh` CLI command. Bearer/API-token
support remains the preferred long-term authentication path when Enji exposes
it.

## Audit Discovery

Every command in the Audit Catalog context fetches `GET /api/ux/catalog` once
per invocation. The `curatedActions` array is authoritative: published audits
in the live response define the available audits, so newly published audits
participate automatically. The CLI maintains
`~/.config/enji-guard/state/audit-catalog.json` only as the previous observation
for detecting added, removed, or changed audits. It is never an API fallback or
selector source. The first valid response establishes a baseline without a
business notice; later differences are emitted as a text business notice on
stdout. JSON places the notice data in the stable top-level `audit_catalog`
business section, with `changes: []` when there are no changes. stderr is
reserved for errors.

CLI report selectors are action-key suffixes without the `audit.` prefix. For
example, selector `security` identifies action key `audit.security`; the exact
action key from the catalog is used for API requests. Recon is the separate
`audit.recon` action and is not part of the report-audit selector set.

Catalog parsing is intentionally strict. The current guard requires exactly one
`audit.recon` action, treats published report audits as non-recon
`category == "audit"` entries with `status == "published"`, requires a
`metricGroup` for each published report audit, and rejects duplicate action
keys or duplicate CLI selectors.

## Repository Lifecycle

Repository identity in the client is provider-neutral: `(provider, host,
locator)`. GitHub locators contain exactly two path segments; GitLab locators
may contain nested groups (for example `group/subgroup/service`) and are paired
with the GitLab host. The CLI selector form is always `provider@host:locator`.

Repository onboarding is a client-orchestrated sequence, not one atomic backend
operation:

1. `POST /api/ux/projects/{projectId}/repos` creates membership and returns a
   repository ID.
2. `PUT /api/ux/projects/{projectId}/repos/{repoId}/connection` marks verified
   GitHub App access as connected.
3. `POST /api/ux/repos/{repoId}/audit-runs` with `audit.recon` starts baseline
   diagnostics.

The backend does not implicitly start recon after the first operation. A client
implementing `repo add` must perform the full idempotent sequence. The add
request is provider-specific: GitHub sends `{githubOwner, githubName}`;
GitLab sends `{provider: "gitlab", host, repoPath, repoAccessCredentialId}` to
the same project-repository route. GitLab requires an explicit Enji access
credential ID. Available GitHub repositories and access verification are
exposed under `/api/v1/github/app/installations/{installationId}/repos` and
`/verify`.

The current GitLab add helper in `star-button-BvtzxAJl.js` additionally forwards
`providerRepoId`, `webUrl`, `cloneHttpUrl`, `cloneSshUrl`, `apiBaseUrl`, and
`repoAccessCredentialNameSnapshot` when available. The canonical required
identity remains provider, host, locator, and credential ID; these extra fields
are provider metadata, not a second repository identity.

Project creation also spans two resources: create the Fleet project through
`POST /api/v1/projects`, then create its UX projection through
`POST /api/ux/projects`. Fleet deletion is authoritative; deleting only the UX
projection does not reliably remove a project. Product code additionally
forbids deletion of non-empty projects.

Repository transfer uses `transfer/preflight` followed by `transfer`, including
schedule replacements returned or required by preflight.

## Audit Runs And Reports

`POST /api/ux/repos/{repoId}/audit-runs` starts recon or a published catalog
audit. The body
contains `projectId`, `actionKey`, a small `fleetTaskBody`, and a unique
`clientRequestId`. Enji owns the actual runbook selected by the action key.

Progress can be reconciled from three projections:

- repository `active-runs` for the current aggregate view;
- `/api/v1/tasks/{taskId}` and `/activities` for authoritative task-by-ID state;
- repository `audit-rerun-state` for current SHA, last audited SHA, rerun
  eligibility, and the last Fleet task ID.

Freshness is SHA-based only. The current implementation compares the current
repository head SHA with the last audited SHA and classifies reports as
`fresh`, `stale`, or `unknown`; branch names are not part of the freshness
decision. Aggregate audit status also surfaces `partial` and `mixed` states, so
stale but still readable reports remain available while new audits run.

The current report body is returned by
`GET /api/ux/repos/{repoId}/snapshots/upfront.audit.summary?group=<report-group>`
as Markdown in `snapshot.content.report`. The SPA still copies this payload
client-side. It now also calls two endpoints from
`audit-report-archive-DgvcmYa7.js` that are missing from OpenAPI:

- `GET /api/ux/repos/{repoId}/audit-reports?group=<report-group>`, reading
  `reports`;
- `GET /api/ux/repos/{repoId}/audit-reports/archive`, consuming the raw body as
  a download blob.

`GET /api/ux/repos/{repoId}/audit-history` returns score time series keyed by
report group. Each point contains `when`, `score`, `fleetTaskId`, and the
`bad`/`warn`/`good`/`skip` band counts. Treat score deltas as noisy; reductions
in high-value negative bands are the more useful trend signal.

### Report Language

Report language is account-wide, not independently writable per project.
`GET /api/ux/user-preferences` returns `preferences.language`; `PUT` to the same
resource with `{"language":"en"}` or `{"language":"ru"}` changes it. The
project-scoped `GET /api/ux/projects/{projectId}/run-language` returns the
effective language Enji will use for that project's future runs. There is no
observed project-scoped language write endpoint.

## Curated Autofix Management

The workflow is audit -> findings -> optional improvement. `GET /api/ux/catalog`
publishes `auditAutofixes` entries with `actionKey`, `variantKey`, `title`,
`description`, `fleetRunbookId`, `status`, and `sortOrder`.
`improvement-jobs` is the canonical autofix management resource, exposed to the
operator CLI as list/set; retain its GET/PUT job schemas. It is not an audit
schedule and does not replace `audit-auto-runs`.

The current typed relationship registry supports `security` -> `vuln-fix`,
`tests` -> `test-writing`, and `dependency-hygiene` -> `dependency-update`.
Pentest is separate. Batch operations are explicit client-side loops over a
single `REPO`, `--all-repos` within `--project`, or `--all-projects`; no wider
scope is inferred. MCP remains read-only. Remove the temporary relationship
registry when Enji exposes relationships directly. The current CLI only exposes
published autofix entries that are not part of the separate pentest action set;
unsupported autofix selectors remain blocked until a typed relationship is
defined.

## Scheduling

There are two schedule families:

- `audit-auto-runs/{actionKey}` is the current path for automatic audit runs.
  `{actionKey}` is the exact action key published in `curatedActions`, such as
  `audit.security`; do not synthesize keys from display names or CLI selectors.
- `improvement-jobs/{kind}` is for autofix jobs only. It is never an audit
  scheduling fallback and must not be used to identify or schedule report
  audits.

There is no server-side project batch endpoint. The SPA applies project-wide
settings with a client-side loop over repositories and audit action keys. CLI
batch behavior is likewise an explicit client-side loop over the selected
repositories and audits; it must never infer an all-project or all-repository
scope.

An audit schedule uses the exact subscription fields `cadence`, `enabled`,
`scheduleDay`, `scheduleDayOfMonth`, `scheduleTime`, `scheduleTimeSource`,
`timezone`, `windowDays`, `windowEndTime`, `windowMode`, and `windowStartTime`.
The per-subscription `timezone` is IANA; `windowDays` is preserved alongside
the cadence.

The run time is either user-selected or `auto` (Enji-assigned). Auto schedules
use `scheduleTime: "00:00"` with `scheduleTimeSource: "auto"`. `schedule
auto-time` restores those values without changing cadence or timezone. The
container runs in the host timezone, but each subscription's stored IANA
timezone controls that subscription's schedule.

When an audit schedule does not exist yet, the current client-side defaults are
`cadence: "workdays"`, `scheduleTime: "00:00"`,
`scheduleTimeSource: "auto"`, and `timezone: "UTC"` unless the operator
supplies another IANA timezone such as `Asia/Almaty`.

The schedule-load request contains `from`, `to`,
`bucket_seconds=1800`, `candidate_step_seconds=1800`, and
`candidate_duration_seconds`; `timezone` is optional in the current bundle.
It returns 30-minute load buckets plus `candidates`, from which the SPA reads
`rank` and `at`. The SPA selects a low-load candidate and persists the chosen
clock time as `auto`.

The same bundle chunk directly manages Fleet schedules through
`GET|POST /api/v1/schedules`, `GET|PUT /api/v1/schedules/{scheduleId}`, and
`POST /api/v1/schedules/{scheduleId}/toggle` with `{"enabled": <bool>}`.
Collection lookup sends `project_id`, `search`, and `limit=100`; creation reads
`id` or `schedule.id`, while item reads accept `next_run_at` or
`schedule.next_run_at`. These Fleet schedule operations are contract gaps.
Provider-aware `repo_access_contexts` use `{provider: "github",
repo_full_name}` for GitHub and `{provider: "gitlab", host, repo_path,
repo_access_credential_id}` for GitLab, with optional provider ID, web/clone/API
URLs, and credential-name snapshot.

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

## Current Bundle Reconciliation

The following inventory records exact request construction and response
consumption in the 2026-07-21 public bundle. Operations described as present
are still current in the reconstructed OpenAPI. Operations described as gaps
need controlled live verification before contract promotion.

### Provider, GitLab, And Credentials

`github-app-BQBaMpt8.js` functions `P`, `x`, `Q`, `O`, and `N` show:

- Fleet `POST /api/v1/github/app/install-intents` sends `return_to` and optional
  `requested_repo_full_name`, then reads `github_app_install_url`;
- Fleet `GET /api/v1/github/app/status` reads `installations`;
- installation repository listing sends `page` and `per_page`; verification
  sends `owner_repo`;
- UX installation claim sends `install_intent_id`, `installation_id`,
  `account`, and `account_type`, then reads `installation`.

Install intents and status are contract gaps. Installation repository
list/verify and UX installation list/claim are present and still current.

`repo-provider-icon-DSU2kwin.js` functions `R`, `$`, `j`, `x`, `N`, `A`, and
`k` show a Fleet credential/GitLab surface that is entirely absent from the
contract:

- `GET /api/v1/credentials` sends `credential_type=git`, `provider=gitlab`,
  `scope_type`, optional `scope_owner`, and `limit=100`;
- `POST /api/v1/credentials` sends `name`, `credential_type`, `provider`,
  `scope_type`, secret `value`, optional `scope_owner`, and metadata
  `auth_method`, `git_host`, `api_base_url`, `git_user_name`, and
  `git_user_email`;
- `POST /api/v1/credentials/{id}/validate`,
  `POST /api/v1/credentials/{id}/replace-secret` with `{value}`,
  `PATCH /api/v1/credentials/{id}` with `{name}`, and
  `DELETE /api/v1/credentials/{id}`;
- `POST /api/v1/gitlab/credentials/{id}/probe` sends `host`, `api_base_url`,
  `scope_type`, and optional `scope_owner`;
- `GET /api/v1/gitlab/projects` also sends `credential_id`, `search`, `page`,
  and `per_page`.

Credential UI response usage is `id`, `name`, `credential_type`, `provider`,
`scope_type`, `scope_owner`, `status`, `last_error`, `expires_at`, and metadata
`git_host`, `api_base_url`, `gitlab_health_reason`. Project selection maps
`path_with_namespace`, `provider_project_id`, `web_url`, `clone_http_url`,
`clone_ssh_url`, and `api_base_url` in `star-button-BvtzxAJl.js`.

The CLI now exposes the two read-only discovery operations without exposing
credential secrets or clone URLs:

- `GET /api/v1/credentials?credential_type=git&provider=gitlab` accepts the
  optional `scope_type`, `scope_owner`, `limit`, and `offset` filters and
  returns `{data,meta:{limit,offset,total}}`.
- `GET /api/v1/gitlab/projects` accepts `credential_id`, optional credential
  endpoint metadata (`host`, `api_base_url`), `search`, `page`, `per_page`, and
  scope filters, returning `{data,meta:{next_page}}`.

`gitlab credentials` and `gitlab projects` translate these envelopes through a
narrow typed gateway. Projects resolve exactly one credential (or require an
explicit `--credential-id`), validate the provider URLs, and can follow
`next_page` sequentially with `--all-pages`; cycles and duplicate provider IDs
are rejected. The resulting selector is `gitlab@host:path_with_namespace`,
ready for `repo add`.

### Catalog, Triggers, Project, And Repository

Catalog reads of `curatedActions` and `auditAutofixes` remain current.
Code-review trigger GET/PUT remains current and its PUT body is
`{autoOnPrOpen, onMention}`. Existing project, repository, connection,
transfer, web-resource, favorites/order, publication, public dashboard,
run-language, access, task-link, and notification operations are all still
represented in OpenAPI. Provider-aware GitLab repository metadata described in
Repository Lifecycle is newer than the minimal required create schema and
should be verified before the contract is tightened.

### Reports, Audits, And Executive Summaries

The audit-report gaps are documented in Audit Runs And Reports. Existing audit
run, snapshot, history, active-run, rerun-state, email preference, and
email-watch operations remain current. `audit-rerun-state-BsqViZwq.js` reads
`state`.

`use-audit-run-CoB5D8ZT.js` confirms these previously incomplete payloads:

- findings PUT sends `auditActionKey`, `reportGroup`, `auditFleetTaskId`, and
  `content`; findings GET sends `run` and `group`, and both read `findings`;
- finding autofix-result POST sends `autofixFleetTaskId`, `auditFleetTaskId`,
  `status`, and nullable `issueUrl`, `prUrl`, `mergedAt`, `completedAt`, and
  `errorMessage`, then reads `finding`;
- audit email-watch POST sends `projectId`, `actionKey`, and `fleetTaskId`;
- audit run POST sends `projectId`, `actionKey`, `fleetTaskBody`, and
  `clientRequestId`.

`executive-summaries-B3Vq6tIK.js` functions `t`, `u`, `o`, `s`, and `r` show
that repository list GET reads `runs`, availability GET is returned raw,
collection POST forwards a summary request object, item DELETE has no body, and
public GET reads `summary`. Only repository list GET is present in OpenAPI;
availability, collection POST, item DELETE, and public GET are gaps. The
forwarded POST schema is uncertain.

### Schedule And Autofix

The improvement-job, binding, backlog, resume, run, and tried operations remain
current. `fleet-aware-schedule-time-ZojCwCV3.js` shows that job PUT sends
`enabled`, `autofixVariantKey`, `autoFix`, `frequency`, `daysOfWeek`,
`scheduleTime`, `scheduleTimeSource`, `timezone`, and `pentestMode`; binding PUT
sends `fleetScheduleId` and `autofixVariantKey`. Improvement-run POST sends
`projectId`, `actionKey`, `autofixVariantKey`, `fleetTaskBody`, and optional
`auditFinding`, then reads `linkCreated` and `task`. Fleet schedule contract
gaps are documented in Scheduling.

### Account And Admin

`settings-BceUw6GW.js` functions `Se` and `Le` use
`GET|PUT /api/ux/email-preferences`, reading `preferences`; PUT forwards the
edited preference object. Both operations are gaps, and the complete write
schema is uncertain.

Except for `/api/ux/admin/auth/check`, the admin calls in
`admin-COdxBH64.js` are absent from OpenAPI:

- functions `Ne`, `ze`, and `Qe`: metric-group collection GET/POST and item
  PUT, reading `metricGroups` or `metricGroup`;
- functions `Je`, `Xe`, `Ze`, `ae`, `et`, and `it`: audit collection GET/POST,
  item GET/PUT, cascade-preview GET, and propagate POST with an object defaulting
  to `{}`, reading `curatedActions`, `curatedAction`, or `preview`;
- audit-log GET sends `limit` plus optional `before`, `entityType`, and
  `entityId`; access-log GET sends `limit` plus optional `before`. The UI reads
  audit actor/action/entity and access method/path/status/duration fields;
- users GET sends optional `q`, `limit`, and `offset`; user-group PUT sends
  `{group}`. The UI reads `id`, `name`, `email`, `group`, and
  `adminBroadcastEmails`;
- functions `nt` and `rt`: broadcast preview POST sends `language`, `subject`,
  `preheader`, and `messageMarkdown`, reading `html`, `subject`, and `preheader`.
  Send POST adds audience `{type: "all"}` or
  `{type: "users", userKeys: [...]}` and `clientRequestId`, reading `sent`,
  `failed`, `skippedOptedOut`, and `skippedNoEmail`;
- propagate-run list GET sends optional `actionKey` and `limit`; item GET is
  raw. Cancel, resume, rollback, and retry-failed POST `{}`; rollback and retry
  read `runId`;
- functions `ta` and `aa`: executive-summary settings GET and PUT, with PUT
  body `{fleetRunbookId}` where the value may be null.

Complete metric-group and audit write schemas remain uncertain.

### Confirmed Contract Gaps

There are 49 confirmed-current operations missing from the reconstructed
contract: auth (3), GitHub App (2), credentials/GitLab (8), Fleet schedules
(5), audit reports (2), executive summaries (4), account email preferences
(2), and admin (23). This is an evidence inventory, not authority to add
permissive operations. A successful controlled request should establish status
codes and schemas before the contract is updated.

## Known Gaps

These endpoints were found but their complete request or response schema was not
captured:

- pentest job updates and pentest consent updates;
- executive-summary creation;
- account email-preference updates;
- admin metric-group and audit writes;
- member roles beyond `owner` and `member`;
- the public snapshot identifier format;
- website SEO and user-testing audit surfaces.

Existing operations with incomplete payloads remain permissive in OpenAPI and
carry an observation marker. Missing operations should not be added, and
existing schemas should not be tightened, until a live request or successful
controlled probe supplies the missing evidence.
