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
- [ ] Сделать MCP compact-first: по умолчанию status/summary, полные Markdown
  bodies — только по явным audit selectors. **Evidence:** MCP contract-тесты
  default и explicit selector. **Области:** MCP facade, MCP delivery, Audit reads.
- [ ] Унифицировать либо доказать parity CLI/MCP JSON projection: semantic nulls,
  provider-neutral repository identity и детерминированные DTO fields; не
  допускать тихого drift. **Evidence:** общие fixture/contract-тесты обеих
  поверхностей. **Области:** Application views, CLI, MCP, Portfolio identity.
- [ ] Зафиксировать clarification: stateless MCP — stateless protocol, а не
  отсутствие application state; локальные записи `FileAuditLedger` и
  `AuditCatalogObserver` сами по себе не дефект. **Evidence:** decision/docs
  update и тесты, подтверждающие protocol statelessness без запрета ledger/catalog
  persistence. **Области:** MCP architecture, Audit persistence, docs.

## Slice 2 — безопасные mutation workflows

- [ ] Валидировать IANA timezone до выбора targets и до первого write для audit
  schedules и improvement jobs. **Evidence:** tests доказывают zero gateway writes
  при невалидной timezone. **Области:** `audit/scheduling.py`, CLI selectors,
  improvement jobs, gateway boundary.
- [ ] Ввести единый batch mutation result для target/selector:
  `changed|unchanged|failed`, `reason`, явные `partial result/completed/remaining`;
  email не делает слепой PUT при no-op. **Evidence:** sequential batch и no-op
  email contract-тесты. **Области:** Application mutation orchestration, email,
  schedules, improvement jobs, CLI JSON.
- [ ] Убрать source-grep negative-archaeology test о improvement/runbook и заменить
  его behavioral assertion на уровне port. **Evidence:** новый port-level тест
  сначала red при нарушении поведения, затем green; source-grep удалён.
  **Области:** tests, improvement-jobs port/application contract.

## Slice 3 — язык, границы и композиция

- [ ] Принять `improvement job` как внешний ubiquitous language: autofix — его
  свойство/вариант, не имя сущности; произвольные provider `extensions` не
  публикуются как стабильный application/JSON contract. **Evidence:** DTO/API
  contract-тесты и user-facing terminology review. **Области:** Audit Catalog,
  Application views, CLI docs/JSON.
- [ ] Удалить неиспользуемый `runtime_auth` из `AuthFacade` и
  `create_application`; coordinator использует только `runtime_auth_service`.
  **Evidence:** type/import tests и tach проходят без прежней зависимости.
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

- [ ] Исправить текущую MCP lifecycle concurrency проблему общего mutable `active`
  facade без MCP v2 и добавить concurrent acceptance test. **Evidence:**
  воспроизводящий concurrent test red→green и отсутствие cross-request facade
  leakage. **Области:** MCP lifecycle/runtime, facade factory, tests.
- [ ] Ратчетить basedpyright unknown diagnostics по production packages, не
  включая strict на весь repo одним махом. **Evidence:** baseline и поэтапно
  ужесточённые production diagnostics в CI. **Области:** basedpyright config,
  production packages, CI.
- [ ] После измерения устранить двойной полный `pytest` прогон в `verify`, сохранив
  coverage, CRAP и parallel semantics. **Evidence:** измерение before/after,
  `just verify` сохраняет все три гарантии. **Области:** Justfile/QA scripts, CI.

## Deferred / roadmap-only

- [ ] Оставить feedback loop отдельным узким use case; проверить, что он явно есть
  в roadmap, но сейчас не раздувать Audit/Subscriptions. **Evidence:** ROADMAP
  содержит endpoint/use case и явно исключает расширение текущих contexts.
  **Области:** roadmap, future Application feedback boundary.
- [ ] После появления API keys переопределить или удалить health/readiness после
  удаления daemon; сейчас только roadmap из-за внешнего blocker. **Evidence:**
  roadmap criterion привязан к API-key availability. **Области:** roadmap,
  Runtime/Observability, auth.
- [ ] Зафиксировать PyPI trusted publishing и Windows/`fcntl` решение как будущий
  package-first этап; implementation зависит от API-key cutover и owner publishing
  setup. **Evidence:** roadmap с явными blockers/owner assumptions. **Области:**
  packaging roadmap, CI publishing, auth/storage portability.
- [ ] MCP 2026-07-28 / Python SDK v2 — только roadmap: не обновлять dependency и
  не внедрять SDK v2 в этой работе; после quarantine удалить legacy
  SSE/mount-path/initialize/initialized/session-header и переписать release
  smoke/contract. **Evidence:** deferred checklist с quarantine exit criteria и
  обновлённым post-upgrade smoke scope. **Области:** roadmap, MCP dependency,
  release QA.
- [ ] После API keys буквально удалить cookie variant, refresh state machine,
  journal/outbox, watcher, refresh task и cached readiness plumbing; только
  roadmap в этой работе. **Evidence:** roadmap перечисляет delete-set и API-key
  blocker. **Области:** Auth Session, Runtime/Observability, Docker.
- [ ] Добавить package artifact/build/install gates и подготовить publishing path
  без фактической публикации, если не нужна внешняя authority; явно отделить от
  Docker image gate. **Evidence:** CI/package gate design и publishing readiness
  checklist без publish side effect. **Области:** packaging, CI, release docs.

## Explicit non-actions

- [ ] Не делить большие `http.py`/`app.py`/auth-файлы без co-change или conflict
  pressure. **Evidence:** PR rationale фиксирует конкретное pressure перед любым
  split. **Области:** gateway HTTP, application, Auth Session.
- [ ] Не схлопывать application views в domain DTO, не объединять Audit/Portfolio
  ports и не упрощать auth state machine ради LOC. **Evidence:** architecture
  review и tach/contract tests сохраняют границы. **Области:** Application,
  Audit, Portfolio, Auth Session.
- [ ] Сохранить CLI no-args help/status-first mental model без неявного сетевого
  dashboard. **Evidence:** CLI startup/no-args tests и documentation contract.
  **Области:** CLI UX, delivery, tests.

## Final gates

Завершение implementation slice требует его указанного acceptance evidence,
обновлённых контрактов/документации и `just verify`; package-first evidence
отчитывается отдельно от Docker image/runtime gate.
