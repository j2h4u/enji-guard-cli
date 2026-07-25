# Architecture cleanup: the work order behind a red `tach`

`tach.toml` declares the **ideal** module graph, not the current one.  Running
`just module-boundaries` therefore fails on purpose.  This document is the
complete inventory of those failures: what breaks, why the code got that way,
and the concrete change that fixes it.

`just module-boundaries` is **not** part of `just check`.  `import-linter`
(contracts in `pyproject.toml`) stays the enforced gate until this document is
empty; at that point `module-boundaries` joins `check` and the import-linter
contracts are deleted.

Baseline for this inventory was **41 failures** — 23 interface, 18 layer/visibility.
Fixes 1, 2 and 3 have landed; **19 failures remain**, all of them Fix 4.

## The declared layers

Highest first.  A module may depend downward once the edge is written in
`depends_on`; upward is never allowed; same-layer edges must be declared.

| Layer | Modules | Why it sits here |
|---|---|---|
| `delivery` | `delivery.cli`, `delivery.mcp` | argv, stdout, MCP wire protocol |
| `composition` | `composition` | the only module that knows concretes exist |
| `facade` | `mcp_facade` | read-only projection of the application for agents |
| `application` | `application` | use cases; owns the operator-visible vocabulary |
| `observability` | `runtime_observability` | supervision, telemetry, readiness, journeys |
| `gateway` | `enji_gateway` | anti-corruption over the Enji HTTP API |
| `domain` | `audit`, `portfolio`, `gitlab` | bounded contexts |
| `credentials` | `auth_session` | auth material; below everyone who consumes it |
| `transport` | `transport` | HTTP mechanics |
| `foundation` | `settings`, `fanout`, `atomic_json`, `errors`, `json_types`, `transport_types`, `version`, `_build_provenance` | leaf utilities |

Two same-layer edges are declared explicitly: `portfolio -> audit` (allowed
only through `audit.ports`, pinned by an interface) and `gitlab -> portfolio`
(GitLab discovery reuses `RepositoryIdentity` rather than minting a second
one).  `delivery.cli -> delivery.mcp` is the third: `guard mcp serve` boots
the server in-process.

---

## ~~Fix 1 — invert `auth_session -> enji_gateway`~~ — DONE

**Size: medium.  Do this first: it is the only cycle, and it is the reason
`forbid_circular_dependencies` cannot be trusted until it lands.**

```
[FAIL] src/enji_guard_cli/auth_session/adapters.py:10: The path 'enji_guard_cli.enji_gateway.ports.GatewayCredentialError' is not part of the public interface for 'enji_guard_cli.enji_gateway'.
[FAIL] src/enji_guard_cli/auth_session/adapters.py:13: The path 'enji_guard_cli.enji_gateway.ports.GatewayCredentialReader' is not part of the public interface for 'enji_guard_cli.enji_gateway'.
[FAIL] src/enji_guard_cli/auth_session/adapters.py:10: Cannot use 'enji_guard_cli.enji_gateway.ports.GatewayCredentialError'. Layer 'credentials' ('enji_guard_cli.auth_session') is lower than layer 'gateway' ('enji_guard_cli.enji_gateway').
[FAIL] src/enji_guard_cli/auth_session/adapters.py:13: Cannot use 'enji_guard_cli.enji_gateway.ports.GatewayCredentialReader'. Layer 'credentials' ('enji_guard_cli.auth_session') is lower than layer 'gateway' ('enji_guard_cli.enji_gateway').
```

**Why the code does it.**  `enji_gateway/ports.py` declares the protocol
`GatewayCredentialReader` and the error `GatewayCredentialError` that the HTTP
client needs, and `auth_session/adapters.py` implements the protocol.  So the
implementer imports the abstraction from its consumer.  Note that the protocol
is already *typed in terms of `auth_session`*: both its methods speak
`auth_session.models.StoredAuth`, and `enji_gateway/ports.py:6` imports it.
The abstraction is a credential-reading capability; it belongs to the module
that owns credentials, not to the module that happens to call it.

**Fix.**

1. Move `GatewayCredentialError` and the `GatewayCredentialReader` protocol
   out of `src/enji_guard_cli/enji_gateway/ports.py` into
   `src/enji_guard_cli/auth_session/ports.py`, renamed to `CredentialError`
   and `CredentialReader` (nothing about them is gateway-specific any more).
   Add both to `auth_session/__init__.py`'s `__all__`.
2. `enji_gateway` imports them from `enji_guard_cli.auth_session` — a
   downward, already-declared edge.  `GatewayAuthFile` and `GatewayClient`
   stay in `enji_gateway/ports.py`; they are genuinely gateway concerns.
3. Delete the two imports and the `GatewayCredentialReaderPort` alias at
   `auth_session/adapters.py:9-14`; the class just subclasses the local
   protocol (or drops the base entirely, since it is structural).

**Also clears:** the last remaining use of `deprecated = true` in the old
config, and the `enji_gateway.visibility` violation (nothing below the gateway
may see it).

**Note on `forbid_circular_dependencies`.**  The flag validates the *declared*
graph, not the import graph, so it is green today simply because the ideal
graph is acyclic.  It is on as a ratchet: it makes it impossible to ever
"resolve" a future cycle by writing it into `tach.toml`.

---

## ~~Fix 2 — import `auth_session` through its package seam~~ — DONE

**Size: small.  Independent of everything else.**

```
[FAIL] src/enji_guard_cli/application/auth.py:6: The path 'enji_guard_cli.auth_session.api.AuthError' is not part of the public interface for 'enji_guard_cli.auth_session'.
[FAIL] src/enji_guard_cli/application/auth.py:7: The path 'enji_guard_cli.auth_session.models.AuthSessionStatus' is not part of the public interface for 'enji_guard_cli.auth_session'.
[FAIL] src/enji_guard_cli/application/auth.py:7: The path 'enji_guard_cli.auth_session.models.ImportCredentialPayload' is not part of the public interface for 'enji_guard_cli.auth_session'.
[FAIL] src/enji_guard_cli/application/auth.py:8: The path 'enji_guard_cli.auth_session.service.AuthSessionService' is not part of the public interface for 'enji_guard_cli.auth_session'.
[FAIL] src/enji_guard_cli/enji_gateway/client.py:8: The path 'enji_guard_cli.auth_session.models.StoredAuth' is not part of the public interface for 'enji_guard_cli.auth_session'.
[FAIL] src/enji_guard_cli/enji_gateway/ports.py:6: The path 'enji_guard_cli.auth_session.models.StoredAuth' is not part of the public interface for 'enji_guard_cli.auth_session'.
```

**Why the code does it.**  `auth_session/__init__.py` already publishes a
curated seam, and `AuthSessionStatus`, `AuthSessionService` and `StoredAuth`
are already in its `__all__` — the call sites simply reach past it out of
habit.  `AuthError` and `ImportCredentialPayload` are genuinely missing from
the seam.

**Fix.**  Add `AuthError` and `ImportCredentialPayload` to
`auth_session/__init__.py` (`from ... import` plus `__all__`), then rewrite the
five import statements to `from enji_guard_cli.auth_session import ...`.

---

## ~~Fix 3 — import `gitlab` through its package seam~~ — DONE

**Size: small.  Independent; do it with Fix 2.**

```
[FAIL] src/enji_guard_cli/application/gitlab.py:5: The path 'enji_guard_cli.gitlab.models.GitLabCredentialsResult' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/application/gitlab.py:5: The path 'enji_guard_cli.gitlab.models.GitLabProjectsQuery' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/application/gitlab.py:5: The path 'enji_guard_cli.gitlab.models.GitLabProjectsResult' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/application/gitlab.py:6: The path 'enji_guard_cli.gitlab.ports.GitLabDiscoveryPort' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/enji_gateway/gitlab_gateway.py:12: The path 'enji_guard_cli.gitlab.models.GitLabCredential' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/enji_gateway/gitlab_gateway.py:13: The path 'enji_guard_cli.gitlab.models.GitLabCredentialPage' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/enji_gateway/gitlab_gateway.py:14: The path 'enji_guard_cli.gitlab.models.GitLabCredentialsResult' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/enji_gateway/gitlab_gateway.py:15: The path 'enji_guard_cli.gitlab.models.GitLabProject' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/enji_gateway/gitlab_gateway.py:16: The path 'enji_guard_cli.gitlab.models.GitLabProjectPage' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/enji_gateway/gitlab_gateway.py:17: The path 'enji_guard_cli.gitlab.models.GitLabProjectsQuery' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/enji_gateway/gitlab_gateway.py:18: The path 'enji_guard_cli.gitlab.models.GitLabProjectsResult' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/enji_gateway/gitlab_gateway.py:19: The path 'enji_guard_cli.gitlab.models.GitLabScope' is not part of the public interface for 'enji_guard_cli.gitlab'.
```

Three more of the same kind disappear as a side effect of Fix 4, because the
import statements themselves go away:

```
[FAIL] src/enji_guard_cli/delivery/cli/app.py:49: The path 'enji_guard_cli.gitlab.models.GitLabProjectsQuery' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:13: The path 'enji_guard_cli.gitlab.models.GitLabCredentialsResult' is not part of the public interface for 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:13: The path 'enji_guard_cli.gitlab.models.GitLabProjectsResult' is not part of the public interface for 'enji_guard_cli.gitlab'.
```

**Why the code does it.**  Pure habit.  Every one of these names is already in
`gitlab/__init__.py`'s `__all__`.

**Fix.**  Rewrite the imports to `from enji_guard_cli.gitlab import ...`.  No
production code changes beyond the import lines.

---

## Fix 4 — get the domain out of `delivery.cli`

**Size: large.  This is the real work.  Do it after Fixes 2 and 3 so that
`application`'s seam is already the habitual place to look.**

Sixteen layer/visibility failures, all one problem: the CLI types its
presenters and its Typer options against domain objects, so every domain
rename is a CLI change and the "application layer owns the operator
vocabulary" claim is fiction.

### 4a — `audit` types in the CLI (8 failures)

```
[FAIL] src/enji_guard_cli/delivery/cli/app.py:28: Cannot use 'enji_guard_cli.audit.email.EmailPreferencesUpdate'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.audit'.
[FAIL] src/enji_guard_cli/delivery/cli/app.py:29: Cannot use 'enji_guard_cli.audit.ports.AuditAutofixUpdate'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.audit'.
[FAIL] src/enji_guard_cli/delivery/cli/app.py:29: Cannot use 'enji_guard_cli.audit.ports.AuditScheduleUpdate'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.audit'.
[FAIL] src/enji_guard_cli/delivery/cli/app.py:30: Cannot use 'enji_guard_cli.audit.schedules.CADENCES'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.audit'.
[FAIL] src/enji_guard_cli/delivery/cli/audit_presenter.py:3: Cannot use 'enji_guard_cli.audit.artifacts.AuditRead'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.audit'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:9: Cannot use 'enji_guard_cli.audit.artifacts.AuditRead'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.audit'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:9: Cannot use 'enji_guard_cli.audit.artifacts.AuditSummary'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.audit'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:10: Cannot use 'enji_guard_cli.audit.ports.AuditWaitResult'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.audit'.
```

**Why the code does it.**  Two distinct reasons, which need two distinct fixes:

- *Inbound* (`app.py:28-30`): the CLI constructs domain write-commands from
  argv — `AuditScheduleUpdate(...)` at `app.py:886` and `:933`,
  `AuditAutofixUpdate(...)` at `:973`, `EmailPreferencesUpdate(...)` at
  `:1019` — and reads `CADENCES` at `:216` purely to build the `--frequency`
  help string.
- *Outbound* (`presenters.py`, `audit_presenter.py`): presenter functions are
  annotated with the domain result types they render.

**Fix.**

- Inbound: give `SubscriptionsFacade` primitive-argument methods
  (`set_schedule(*, enabled: bool | None, cadence: str | None, timezone: str | None, ...)`)
  so the CLI passes argv values and the application constructs the domain
  command.  Publish the cadence choices as an application-level constant
  (e.g. `AUDIT_CADENCES: tuple[str, ...]` in `application/__init__.py`) so
  Typer's help text no longer reads a domain module.
- Outbound: define presentation DTOs in the application layer
  (`AuditReadView`, `AuditSummaryView`, `AuditWaitView`) and have the audit
  facade map domain objects onto them.  **Do not re-export `AuditRead` from
  `application/__init__.py`** — that is exactly the laundering the
  `[[interfaces]]` block on `application` exists to catch, and tach will not
  flag it (the import is legal) while leaving the coupling intact.  The check
  that it was done properly is: `grep -r "enji_guard_cli.audit" src/enji_guard_cli/delivery/`
  returns nothing, *and* `application/__init__.py` names no `Audit*` type that
  is defined under `audit/`.

### 4b — `portfolio` types in the CLI (5 failures)

```
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:14: Cannot use 'enji_guard_cli.portfolio.models.ProjectRef'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.portfolio'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:14: Cannot use 'enji_guard_cli.portfolio.models.ProjectSettings'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.portfolio'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:14: Cannot use 'enji_guard_cli.portfolio.models.RepositoryRef'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.portfolio'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:15: Cannot use 'enji_guard_cli.portfolio.status.PortfolioOverview'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.portfolio'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:15: Cannot use 'enji_guard_cli.portfolio.status.RepositoryStatus'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.portfolio'.
```

**Why the code does it.**  Same as 4a-outbound: `repository_label` (`:18`),
`portfolio_text` (`:26`), `repository_status_text` (`:45`),
`project_list_text` (`:108`) and `project_settings_text` (`:112`) are typed
against portfolio domain objects.

**Fix.**  `PortfolioFacade` returns application-owned views
(`PortfolioOverviewView`, `RepositoryStatusView`, `ProjectRefView`,
`ProjectSettingsView`) built from the domain objects.  Same anti-laundering
rule as 4a.

### 4c — `gitlab` types in the CLI (3 failures)

```
[FAIL] src/enji_guard_cli/delivery/cli/app.py:49: Cannot use 'enji_guard_cli.gitlab.models.GitLabProjectsQuery'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:13: Cannot use 'enji_guard_cli.gitlab.models.GitLabCredentialsResult'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.gitlab'.
[FAIL] src/enji_guard_cli/delivery/cli/presenters.py:13: Cannot use 'enji_guard_cli.gitlab.models.GitLabProjectsResult'. Module 'enji_guard_cli.delivery.cli' cannot depend on 'enji_guard_cli.gitlab'.
```

**Why the code does it.**  `app.py:558` builds a `GitLabProjectsQuery` from
argv; `gitlab_credentials_text` (`presenters.py:241`) and
`gitlab_projects_text` (`:252`) render domain results.

**Fix.**  Smallest of the three: `GitLabFacade.list_projects` takes the query
fields as primitives, and returns application-owned views.  Doing 4c first is
a good rehearsal for 4a and 4b — it is the same shape at one third the size.

---

## Summary

| Fix | Failures cleared | Size | Depends on | Status |
|---|---|---|---|---|
| 1 — invert `auth_session -> enji_gateway` | 4 | medium | — | done |
| 2 — `auth_session` package seam | 6 | small | — | done |
| 3 — `gitlab` package seam | 12 (+3 via Fix 4) | small | — | done |
| 4c — `gitlab` out of the CLI | 3 | medium | 3 | open |
| 4b — `portfolio` out of the CLI | 5 | large | — | open |
| 4a — `audit` out of the CLI | 8 | large | — | open |
| | **41** (22 cleared, **19 remaining**) | | | |

Recommended order was 2, 3, 1, 4c, 4b, 4a.  Remaining: 4c, 4b, 4a.

---

## Rules that hold today and are declared as ratchets

These produce no failures.  They are in `tach.toml` so that the next drift is
caught the day it is written, and they are worth knowing about before editing:

- `root_module = "forbid"` — `enji_guard_cli/__init__.py` is empty and stays
  empty; there is no "misc" drawer at the package root.
- `exact = true` — every `depends_on` entry in `tach.toml` corresponds to a
  real import.  Deleting an import without deleting the declaration fails.
- `layers_explicit_depends_on = true` — being lower in the stack is not
  enough; the edge must be written down.
- `ignore_type_checking_imports = false` — an `if TYPE_CHECKING:` import is
  real coupling and is checked like any other.
- `audit.cannot_depend_on = ["portfolio", "gitlab"]` — the long-standing
  "audit stays independent from portfolio" rule, now stated on the audit side
  so no portfolio change can create the edge.
- The `audit` interface visible to `portfolio` and `enji_gateway` exposes only
  `ports.*` and `errors.*` — this is "Portfolio may enter Audit only through
  ports", enforced from Audit rather than from Portfolio.  It also constrains
  the gateway to translating wire payloads into port types.
- The `application` interface exposes top-level names only (`[^.]+`).  Deep
  imports such as `application.subscriptions.AutofixListing` are banned; the
  facades' `__all__` *is* the contract.
- `runtime_observability` shows `application` only `ports.*` — use cases may
  depend on the shape of supervision, never on the supervisor, the logger or
  the sink.
- `transport.visibility` is exactly `enji_gateway` and `auth_session`: those
  two are the only modules that speak HTTP.

## Deliberately not declared

- `data_types = "primitive"` on any interface.  It would force every boundary
  to speak `str`/`int`/`dict` only, which would delete `ApplicationResult`,
  `AuditRead` and every other typed DTO.  The project's dataclass-heavy style
  is a deliberate choice and this flag would fight it.
- An interface on `runtime_observability` restricted to `[^.]+`.  Its
  `__init__.py` is intentionally empty (the package is a namespace of runtime
  concerns, not a seam), so a top-level-only rule would forbid everything and
  force a pointless re-export file.  The interfaces name the allowed
  submodules instead, which excludes `telemetry_sink` — the one genuinely
  private piece.
- Any `deprecated = true` waiver.  A waiver makes a violation look approved,
  which is the opposite of what this file is for.
