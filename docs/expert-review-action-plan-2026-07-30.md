# План действий по экспертному ревью — 2026-07-30

## Scope / Rules

Долговечный checklist для последующих slices. Каждый пункт начинается незавершённым
`[ ]`; отметка допускается только после указанного evidence. MCP SDK v2 и MCP
2026-07-28 implementation **исключены пользователем из этой работы**.

## Slice 1 — контракты чтения и представления

- [x] Всегда включать `audit_catalog.changes` в catalog-aware JSON, в том числе
  пустой массив. **Evidence:** контрактные CLI-тесты для changed и unchanged
  catalog; JSON-снимки; focused pytest 58 passed. **Области:** Audit Catalog, Application, CLI JSON.
- [x] Добавить семантический human presenter для email, отдельный от сырой JSON
  проекции. **Evidence:** golden-тесты читаемого вывода и сохранённого JSON
  контракта; focused pytest 58 passed. **Области:** email preferences, CLI presentation, tests.
- [x] Сделать MCP compact-first: по умолчанию status/summary, полные Markdown
  bodies — только по явным audit selectors. **Evidence:** MCP contract-тесты
  default и explicit selector; focused 89 passed; ruff/typecheck lock green.
  **Области:** MCP facade, MCP delivery, Audit reads.
- [x] Унифицировать либо доказать parity CLI/MCP JSON projection: semantic nulls,
  provider-neutral repository identity и детерминированные DTO fields; не
  допускать тихого drift. **Evidence:** общая fixture/contract-проекция обеих
  поверхностей; serializer CRAP 12.89; focused 89 passed; ruff/typecheck lock green.
  **Области:** Application views, CLI, MCP, Portfolio identity.
- [x] Зафиксировать clarification: stateless MCP — stateless protocol, а не
  отсутствие application state; локальные записи `FileAuditLedger` и
  `AuditCatalogObserver` сами по себе не дефект. **Evidence:** decision/docs
  update; focused 89 passed; ruff/typecheck lock green.
  **Области:** MCP architecture, Audit persistence, docs.

## Slice 2 — безопасные mutation workflows

- [x] Валидировать IANA timezone до выбора targets и до первого write для audit
  schedules и improvement jobs. **Evidence:** zero gateway writes при невалидной
  timezone; focused timezone 34 passed, 5 deselected; ruff/basedpyright green.
  **Области:** `audit/scheduling.py`, CLI selectors, improvement jobs, gateway boundary.
- [x] Ввести единый batch mutation result для target/selector:
  `changed|unchanged|failed`, `reason`, явные `partial result/completed/remaining`;
  email не делает слепой PUT при no-op. **Evidence:** 61 focused passed; email no-op zero PUT;
  ruff/typecheck/module-boundaries green. **Области:** Application mutation orchestration, email,
  schedules, improvement jobs, CLI JSON.
- [x] Убрать source-grep negative-archaeology test о improvement/runbook и заменить
  его behavioral assertion на уровне port. **Evidence:** port-level behavioral
  contract сначала red, затем green; focused 184 passed, 5 deselected; ruff/basedpyright green.
  **Области:** tests, improvement-jobs port/application contract.

## Slice 3 — язык, границы и композиция

- [x] Принять `improvement job` как внешний ubiquitous language: autofix — его
  свойство/вариант, не имя сущности; произвольные provider `extensions` не
  публикуются как стабильный application/JSON contract. **Evidence:** DTO/API
  contract и terminology review; focused 184 passed, 5 deselected; ruff/basedpyright green.
  **Области:** Audit Catalog, Application views, CLI docs/JSON.
- [x] Удалить неиспользуемый `runtime_auth` из `AuthFacade` и
  `create_application`; coordinator использует только `runtime_auth_service`.
  **Evidence:** focused 97 passed; полный `just check` и module-boundaries green
  без прежней зависимости.
  **Области:** composition, application facade, Runtime/Auth boundaries.
- [ ] Разделить composition CLI application и MCP service: обычный CLI при старте
  не импортирует и не тянет MCP runtime. **Evidence:** import/startup test для
  CLI без MCP dependency loading. **Области:** composition root, CLI delivery,
  MCP runtime.
- [ ] Разделить base dependencies и optional MCP extra; обеспечить clean wheel
  install без Docker и отдельный/lazy MCP entrypoint. **Evidence:** build wheel,
  isolated install base package, CLI smoke без Docker, MCP-extra smoke.
  **Области:** `pyproject.toml`, packaging, entrypoints, CI.
- [ ] Предоставить узкий context-managed public package client; не публиковать
  широкий `Application` и внутренние DTO как SDK. **Evidence:** public-import
  contract, context-manager test, API-surface review. **Области:** public package,
  application composition, packaging docs.

## Slice 4 — MCP lifecycle и качество разработки

- [x] Исправить текущую MCP lifecycle concurrency проблему общего mutable `active`
  facade без MCP v2 и добавить concurrent acceptance test. **Evidence:**
  старый mutable test red (оба запроса получили scope `two`), затем green без
  cross-request facade leakage; focused 89 passed; ruff/typecheck lock green.
  **Области:** MCP lifecycle/runtime, facade factory, tests.
- [x] Ратчетить basedpyright unknown diagnostics по production packages, не
  включая strict на весь repo одним махом. **Evidence:** production unknown
  baseline зафиксирован; `just check` green. **Области:** basedpyright config,
  production packages, CI.
- [x] После измерения устранить двойной полный `pytest` прогон в `verify`, сохранив
  coverage, CRAP и parallel semantics. **Evidence:** единый xdist-прогон с branch
  coverage 90.10%, 1033 passed; CRAP 964 functions <= 30.
  **Области:** Justfile/QA scripts, CI.

## Deferred / roadmap-only

- [x] Оставить feedback loop отдельным узким use case; проверить, что он явно есть
  в roadmap, но сейчас не раздувать Audit/Subscriptions. **Evidence:** ROADMAP
  содержит endpoint/use case и явно исключает расширение текущих contexts.
  **Области:** roadmap, future Application feedback boundary.
- [x] После появления API keys переопределить или удалить health/readiness после
  удаления daemon; сейчас только roadmap из-за внешнего blocker. **Evidence:**
  roadmap criterion привязан к API-key availability. **Области:** roadmap,
  Runtime/Observability, auth.
- [x] Зафиксировать PyPI trusted publishing и Windows/`fcntl` решение как будущий
  package-first этап; implementation зависит от API-key cutover и owner publishing
  setup. **Evidence:** roadmap с явными blockers/owner assumptions. **Области:**
  packaging roadmap, CI publishing, auth/storage portability.
- [x] MCP 2026-07-28 / Python SDK v2 — только roadmap: не обновлять dependency и
  не внедрять SDK v2 в этой работе; после quarantine удалить legacy
  SSE/mount-path/initialize/initialized/session-header и переписать release
  smoke/contract. **Evidence:** deferred checklist с quarantine exit criteria и
  обновлённым post-upgrade smoke scope. **Области:** roadmap, MCP dependency,
  release QA.
- [x] После API keys буквально удалить cookie variant, refresh state machine,
  journal/outbox, watcher, refresh task и cached readiness plumbing; только
  roadmap в этой работе. **Evidence:** roadmap перечисляет delete-set и API-key
  blocker. **Области:** Auth Session, Runtime/Observability, Docker.
- [ ] Добавить package artifact/build/install gates и подготовить publishing path
  без фактической публикации, если не нужна внешняя authority; явно отделить от
  Docker image gate. **Evidence:** CI/package gate design и publishing readiness
  checklist без publish side effect. **Области:** packaging, CI, release docs.

## Explicit non-actions

- [x] Не делить большие `http.py`/`app.py`/auth-файлы без co-change или conflict
  pressure. **Evidence:** diff не меняет `enji_gateway/http.py`, не добавляет
  split-файлов; изменения `app.py`/`application/auth.py` — только точечные co-change.
  **Области:** gateway HTTP, application, Auth Session.
- [x] Не схлопывать application views в domain DTO, не объединять Audit/Portfolio
  ports и не упрощать auth state machine ради LOC. **Evidence:** typed application
  views/facades сохранены; `just module-boundaries` и source-policy 19 passed.
  **Области:** Application, Audit, Portfolio, Auth Session.
- [x] Сохранить CLI no-args help/status-first mental model без неявного сетевого
  dashboard. **Evidence:** root callback выдаёт help до composition; focused
  startup/no-args help tests 4 passed; README сохраняет `status`-first contract.
  **Области:** CLI UX, delivery, tests.

## Final gates

Завершение implementation slice требует его указанного acceptance evidence,
обновлённых контрактов/документации и `just verify`; package-first evidence
отчитывается отдельно от Docker image/runtime gate.
