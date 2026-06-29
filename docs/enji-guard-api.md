# Enji Guard — реверс-инжиниринг API

> Составлено по наблюдению живой сессии в браузере (авторизация под `ccc911.ngs.ru@gmail.com`).
> Публичного OpenAPI/Swagger у сервиса **нет** — это восстановленная документация, а не официальный контракт. Поля и пути могут меняться без предупреждения.
> Дата разведки: 2026-06-28.

---

## 1. Архитектура

- **Фронт:** `https://guard.enji.ai/guard-app` — статичный Vite/React SPA. Бизнес-логики на нём нет, только вызовы API.
- **Бэкенд (API):** `https://fleet.enji.ai`
- **Внутреннее кодовое имя приложения:** `upfront` (видно в ключах localStorage). Бэкенд называется `fleet`.

Две поверхности API:

| Префикс | Назначение |
|---|---|
| `/api/ux/*` | BFF под фронт. Агрегированные ответы, «удобные» для UI. Основная рабочая поверхность. |
| `/api/v1/*` | Каноничный REST (`auth/me`, `projects/{id}/members`). Используется реже. |

---

## 2. Авторизация

**Сессия — httpOnly-cookie на домене `.enji.ai`.** Из JS её не прочитать (`document.cookie` показывает только аналитические `_optux_*`). Токена в `localStorage`/`sessionStorage` нет.

Проверка:
```js
fetch('https://fleet.enji.ai/api/v1/auth/me', { credentials: 'include' }) // 200 + профиль
fetch('https://fleet.enji.ai/api/v1/auth/me', { credentials: 'omit'    }) // 401 UNAUTHORIZED
```

`GET /api/v1/auth/me` →
```json
{
  "allowed_clients": ["enji-guard"],
  "client_access_mode": "explicit",
  "current_client": { "id": "enji-guard", "origin": "https://guard.enji.ai",
                      "registration_policy": "open", "status": "enabled" },
  "email": "...", "name": "Maksim", "role": "user", "status": "active", "user_id": "..."
}
```

Заметки для CLI/MCP:
- **API-ключей/PAT нет.** Пути `/api/v1/tokens`, `/api/v1/api-keys`, `/api/v1/clients` → 404.
- Система **многоклиентная** (`allowed_clients`, `registration_policy: "open"`). Теоретически можно зарегистрировать собственный клиент — стоит уточнить у вендора.
- Варианты auth для своей утилиты (по убыванию надёжности): (a) воспроизвести Google-OAuth-флоу headless и хранить cookie-jar; (b) переиспользовать cookie из работающего браузера; (c) запросить программный доступ.
- Все вызовы ниже требуют отправки session-cookie (`credentials: 'include'` в браузере, cookie-jar в CLI) или короткоживущего `Authorization: Bearer <access JWT>`. Bearer access JWT работает, но сам по себе не решает продление сессии.

### ⚠️ Жизненный цикл токена (важно для пакетных операций)
Под cookie живёт **короткоживущий access-token**, который SPA молча обновляет в фоне. Прямые вызовы (CLI/скрипт) этот refresh-цикл не запускают, поэтому при долгом пакетном прогоне токен протухает:
- симптом: внезапный `401 {"error":{"code":"AUTH_INVALID","message":"invalid access token"}}` посреди серии запросов;
- наблюдалось вживую — массовый прогон (~130+ PUT подряд) умер на хвосте; восстановление — релоад/переавторизация и догон оставшихся;
- **вывод для реализации:** клиент обязан (а) ловить `401 AUTH_INVALID` и автоматически освежать сессию с ретраем запроса; (б) дробить пакеты на батчи с паузами/троттлингом, а не лить всё одной очередью.
- Refresh-token одноразово ротируется: успешный `POST /api/v1/auth/refresh` возвращает новый `access_token` и новый `refresh_token` через `Set-Cookie`; старый refresh-token после этого нельзя переиспользовать. CLI обязан сохранять оба `Set-Cookie` в auth-state.

---

## 3. Карта идентификаторов и имён

Самая коварная часть: **слаг отчёта в роуте ≠ kind в API расписания ≠ slug в email-настройках**.

| Отчёт (UI) | Канон-алиас (для CLI/MCP) | Route-слаг (`/repos/{id}/<slug>`) | `kind` в improvement-jobs | `actionKey` (email/run-логи) |
|---|---|---|---|---|
| Безопасность | `security` | `vulns` | `vuln-audit` | `audit.security` |
| Готовность к AI | `ai-readiness` | `ai-readiness` | `ai-maturity` | `audit.ai-readiness` |
| Тесты | `tests` | `tests` | `test-audit` | `audit.tests` |
| Гигиена кодовой базы | `tech-health` | `tech-health` | `tech-health` | `audit.tech-health` |
| Гигиена зависимостей | `deps` | `dependency-hygiene` | `dependency-hygiene` | `audit.dependency-hygiene` |
| Мёртвый код | `dead-code` | `dead-code` | `dead-code` | `audit.dead-code` |
| (Recon / базовая диагностика) | `recon` | — | — (отдельный поток) | `audit.recon` |

**Один отчёт = до 4 разных строк-идентификаторов в зависимости от эндпоинта.** Расписание адресуется по `kind`, email и run-логи — по `actionKey`, маршруты UI — по route-слагу. Любая обёртка обязана прятать это за единым **канон-алиасом** (левый столбец) и резолвить внутри.

Email-настройки (`email-preferences`) адресуются именно по `actionKey` (`audit.*`) и пишут **per-audit override** (подтверждено, см. §6).

### Текущие ID (проект «pets»)

```
projectId = 4de6f2e1-ae0d-4644-94aa-2ec5b614fd50
repoIds = [
  repo_364c507e-1c3e-44c2-916b-9139907321cb,
  repo_dd992a5c-6a9a-4449-a3a2-eb4d82e578b1,
  repo_fd6ad0a0-788e-4efe-b7b8-72ef30012bb8,
  repo_5e484070-e67d-4f93-88e1-934185d32b2a,
  repo_5512b871-7f17-4da6-9b5f-9386525e71cc,
  repo_a3720724-d7de-4a0e-93bd-93c47a8a6a4e,
  repo_91f491c0-3138-4165-bbe5-ed8f137739e7,
  repo_67ea62da-a0db-4026-817b-573453b6d031,
  repo_cc1da464-6d63-4a81-8b51-7018744f7108,
  repo_bf3c9cf4-fb01-44ab-aec1-310922225fa2,
  repo_87d5481c-b88b-4047-9f57-9da84b229f77,
  repo_558e3083-7c38-4327-86ad-d892ff290201,
]
```

---

## 4. Каталог эндпоинтов

Все относительно `https://fleet.enji.ai`. ✅ = наблюдался лично; ✎ = восстановлен из бандла, не дёргался.

### Профиль / доступ
| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/auth/me` ✅ | профиль текущего пользователя |
| POST | `/api/v1/auth/refresh` ✅ | обновить короткоживущий access-token по refresh-cookie; тело не требуется; SPA-like headers `Origin`, `Referer`, `User-Agent`; успех: `{"message":"token refreshed"}` + новые `Set-Cookie` для `access_token` и `refresh_token`; невалидный refresh наблюдался как `401/403` |
| GET | `/api/ux/me/access` ✅ | `{"access": {"group", "fullAccess", "limits", "usage"}}`; в `limits`: schedule/repo/project/member flags, `auditRuns`, `autofixRuns` |
| GET | `/api/ux/user-preferences` ✅ | UI-настройки |
| GET | `/api/ux/catalog` ✅ | `metricGroups`, `curatedActions`, `auditAutofixes` |
| GET | `/api/ux/curated-actions` ✅ | курируемые действия (`workflow.code-review` и пр.), с `fleetRunbookId` |

### Проекты / репозитории
| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/ux/projects` ✅ | список проектов; каждый: `id, name, repoIds[], scores{}, reconPending` |
| GET | `/api/ux/projects/{projectId}/active-runs` ✅ | активные прогоны проекта |
| GET | `/api/v1/projects/{projectId}/members` ✅ | участники |
| GET | `/api/ux/repos/{repoId}/active-runs` ✅ | прогоны репо: `actionKey, fleetTaskId, status, startedAt, completedAt` |
| GET | `/api/ux/repos/{repoId}/executive-summaries` ✅ | сводки |
| GET | `/api/ux/repos/{repoId}/improvement-history?group=improvement.<x>` ✅ | история улучшений |
| GET | `/api/ux/projects/{projectId}/repos/{repoId}/code-review` ✅ | состояние code-review |

### Расписание / автозапуск — **ключевой раздел** (ресурс `improvement-jobs`)
| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/ux/improvement-jobs/{repoId}` ✅ | **все job'ы репо одним вызовом** (по одному на отчёт) |
| GET | `/api/ux/improvement-jobs/{repoId}/bindings` ✅ | мапа `kind → fleetScheduleId` |
| PUT | `/api/ux/improvement-jobs/{repoId}/{kind}` ✅ | **создать/обновить расписание отчёта** |
| POST | `/api/ux/improvement-jobs/{repoId}/{kind}/resume` ✎ | возобновить приостановленный job |
| PUT | `/api/ux/improvement-jobs/{repoId}/{kind}/binding` ✎ | привязка к fleet-расписанию: `{fleetScheduleId, autofixVariantKey}` |
| GET | `/api/ux/schedule-load?…` ✎ | подбор «умного» времени запуска (используется фронтом для `scheduleTimeSource:"auto"`) |

### Email / уведомления
| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/ux/repos/{repoId}/audits/{slug}/email-preferences` ✅ | resolved email-настройки (слоёные) |
| PUT/PATCH | `/api/ux/repos/{repoId}/audits/{slug}/email-preferences` ✎ | переопределить (`override`) |
| — | `/api/ux/repos/{repoId}/audit-email-watches` ✎ | watch'и на email (точная сигнатура не подтверждена — GET по нему отдавал HTML-404) |
| GET/PUT | `/api/ux/task-notifications/{id}` ✎ | нотификации по задачам |

---

## 5. Объект job (расписание отчёта)

`GET /api/ux/improvement-jobs/{repoId}` →
```json
{
  "jobs": [
    {
      "repoId": "repo_67ea62da-...",
      "kind": "vuln-audit",
      "enabled": false,
      "autoFix": false,
      "autofixVariantKey": "default",
      "frequency": "workdays",
      "daysOfWeek": ["mon", "thu"],
      "scheduleTime": "19:13",
      "scheduleTimeSource": "auto",
      "timezone": "Asia/Qyzylorda",
      "pentestMode": "off",
      "triedAt": null
    }
    // ...по одному объекту на каждый из 6 kind'ов
  ]
}
```

### Поля
| Поле | Тип / значения | Смысл |
|---|---|---|
| `enabled` | bool | **главный тумблер** «Запускать автоматически, когда меняется код» |
| `autoFix` | bool | помимо аудита ещё и автофиксить (создавать PR). `false` = только аудит |
| `autofixVariantKey` | string | вариант автофикса, дефолт `"default"` |
| `frequency` | пресет (см. таблицу ниже) | частота. `workdays` = «По будням» |
| `daysOfWeek` | `["mon".."sun"]` | конкретные дни запуска — **независимое поле** (см. ниже) |
| `scheduleTime` | `"HH:MM"` | время запуска |
| `scheduleTimeSource` | `"auto"` \| `"user"` | `auto` = время подобрано сервером (через `schedule-load`); `"user"` = задано вручную. **Не `"manual"`!** (иначе `400 BAD_SCHEDULE_TIME_SOURCE`) |

#### Значения `frequency` (пресеты дропдауна)
| value | label |
|---|---|
| `daily` | Ежедневно |
| `workdays` | По будням |
| `weekly-3x` | Трижды в неделю |
| `weekly-2x` | Дважды в неделю |
| `weekly` | Раз в неделю |
| `monthly` | Раз в месяц |

#### ⚠️ Формат гибкий — `frequency` и `daysOfWeek` независимы
Подтверждено экспериментом: сервер сохраняет **любую** комбинацию `frequency` + `daysOfWeek` и **не выводит** одно из другого. Можно отправить `frequency:"workdays"` с `daysOfWeek:["tue"]`, или `daily` с `["sun"]` — всё сохранится как есть (200).

Дропдаун в UI — это просто пресет, который удобно выставляет **оба** поля разом. Знать «сколько дней у какого пункта» для API не нужно: ты явно передаёшь и `frequency`, и `daysOfWeek`. Для задачи «только по будням» достаточно `frequency:"workdays"` + перечислить нужные дни в `daysOfWeek` (например все пять: `["mon","tue","wed","thu","fri"]`, либо те, что реально нужны).
| `timezone` | IANA TZ, напр. `"Asia/Qyzylorda"` | пояс расписания |
| `pentestMode` | `"off"` \| … | режим авто-пентеста |
| `triedAt` | timestamp\|null | последняя попытка |

### Запись
`PUT /api/ux/improvement-jobs/{repoId}/{kind}`, тело — **полный объект** (PUT идемпотентный, не PATCH):
```json
{
  "enabled": true,
  "autofixVariantKey": "default",
  "autoFix": false,
  "frequency": "workdays",
  "daysOfWeek": ["mon", "thu"],
  "scheduleTime": "19:13",
  "scheduleTimeSource": "auto",
  "timezone": "Asia/Qyzylorda"
}
```
Ответ `200`: `{ "job": { …полный объект… } }`.

> ✅ Подтверждено вживую: PUT `enabled:true` → GET показывает `true`; PUT `enabled:false` → `false`. Состояние при разведке возвращено в исходное.

---

## 6. Email-preferences (resolved-модель) — **запись подтверждена**

**Slug здесь — форма `audit.*` (actionKey), а не route-слаг и не job-kind.** Т.е. `audit.security`, `audit.ai-readiness`, `audit.tests`, `audit.tech-health`, `audit.dependency-hygiene`, `audit.dead-code`.

`GET /api/ux/repos/{repoId}/audits/{actionKey}/email-preferences` →
```json
{
  "resolved": {                         // итоговое значение после слоёв global→project→override
    "digestDaily": false,
    "digestWeekly": true,
    "perRunbookCompletion": true,
    "manualRunCompletion": true,         // чекбокс «о завершении ручного перезапуска»
    "scheduledRunCompletion": true,      // чекбокс «о завершении автоматического аудита»
    "autofixCompletion": true,
    "automationPaused": true,
    "autofixPaused": true,
    "auditCatalogAnnouncements": true,
    "auditDisabledAt": null
  },
  "override": null,   // null = нет переопределения на уровне репо/отчёта
  "project": null,
  "global": { /* … */ }
}
```

**Запись (✅ подтверждено наблюдением):**
```
PUT /api/ux/repos/{repoId}/audits/{actionKey}/email-preferences
body: {"scheduledRunCompletion": true}   // ЧАСТИЧНЫЙ патч — только меняемые поля
```
- Сохраняется **сразу при клике по чекбоксу** (не ждёт кнопку «Сохранить»).
- Меняет слой `override`; в ответе возвращается обновлённый `{resolved, override, ...}`.
- Чекбокс «галочка» в UI отражает **override**, а не resolved: `override.scheduledRunCompletion === true` → галочка стоит.
- Чтобы включить обе галочки: `{"manualRunCompletion": true, "scheduledRunCompletion": true}`.

---

## 7. Рецепт: пакетное включение автопроверки (главная боль)

Задача: на N репозиториях × 6 отчётов включить расписание по будням и настроить email. Через UI это ~6 кликов × 6 отчётов × N репо. Через API — один цикл.

```js
const BASE = 'https://fleet.enji.ai';

// расписание адресуется по job-kind; email — по actionKey. Держим обе формы.
const REPORTS = [
  { kind: 'vuln-audit',         actionKey: 'audit.security' },
  { kind: 'ai-maturity',        actionKey: 'audit.ai-readiness' },
  { kind: 'test-audit',         actionKey: 'audit.tests' },
  { kind: 'tech-health',        actionKey: 'audit.tech-health' },
  { kind: 'dependency-hygiene', actionKey: 'audit.dependency-hygiene' },
  { kind: 'dead-code',          actionKey: 'audit.dead-code' },
];

const api = (path, opt = {}) =>
  fetch(BASE + path, { credentials: 'include', ...opt }).then(async r => {
    if (!r.ok) throw new Error(`${opt.method||'GET'} ${path} -> ${r.status}: ${await r.text()}`);
    return r.status === 204 ? null : r.json();
  });

// Включить ВЕЗДЕ: автозапуск по будням + обе email-галочки
async function setupRepo(repoId, {
  days = ['mon','tue','wed','thu','fri'],   // на какие дни реально ставить запуск
  time = '09:00',
  tz   = 'Asia/Almaty',
} = {}) {
  const { jobs } = await api(`/api/ux/improvement-jobs/${repoId}`);
  for (const { kind, actionKey } of REPORTS) {
    const cur = jobs.find(j => j.kind === kind) || {};
    // 1) расписание по будням
    await api(`/api/ux/improvement-jobs/${repoId}/${kind}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled: true,
        autoFix: false,
        autofixVariantKey: cur.autofixVariantKey ?? 'default',
        frequency: 'workdays',
        daysOfWeek: days,
        scheduleTime: time,
        scheduleTimeSource: 'user',   // ⚠️ "user", НЕ "manual" (иначе 400). Или 'auto' + опустить time.
        timezone: tz,
      }),
    });
    // 2) обе email-галочки
    await api(`/api/ux/repos/${repoId}/audits/${actionKey}/email-preferences`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ manualRunCompletion: true, scheduledRunCompletion: true }),
    });
  }
}

// по всем репозиториям проекта
async function setupAllRepos(projectId) {
  const { projects } = await api('/api/ux/projects');
  const proj = projects.find(p => p.id === projectId);
  for (const repoId of proj.repoIds) await setupRepo(repoId);
}
```

Откат расписания — то же с `enabled:false`. Откат email — `{manualRunCompletion:false, scheduledRunCompletion:false}`. PUT идемпотентны, скрипт можно гонять повторно.

### curl-вариант (для CLI с cookie-jar)
```bash
# расписание по будням
curl -X PUT "https://fleet.enji.ai/api/ux/improvement-jobs/$REPO/vuln-audit" \
  -b cookies.txt -H 'Content-Type: application/json' \
  -d '{"enabled":true,"autoFix":false,"autofixVariantKey":"default","frequency":"workdays","daysOfWeek":["mon","tue","wed","thu","fri"],"scheduleTime":"09:00","scheduleTimeSource":"user","timezone":"Asia/Almaty"}'

# обе email-галочки
curl -X PUT "https://fleet.enji.ai/api/ux/repos/$REPO/audits/audit.security/email-preferences" \
  -b cookies.txt -H 'Content-Type: application/json' \
  -d '{"manualRunCompletion":true,"scheduledRunCompletion":true}'
```

---

## 8. Чего нет / открытые вопросы

- **Официальный OpenAPI/Swagger** — отсутствует (`/openapi.json`, `/api/v1/openapi.json`, `/docs`, `/redoc` → 404/503). Reconstructed-контракт для тестов живёт в `contracts/enji-openapi.json`; источник истины по наблюдениям всё ещё этот документ.
- **API-ключей нет** — auth только через session-cookie.
- `schedule-load` — query-параметры не зафиксированы (используется для `scheduleTimeSource:"auto"`, сервер сам подбирает время).
- `audit-email-watches` — отдельный ресурс, точная сигнатура не подтверждена (для текущей задачи не нужен — галочки идут через `email-preferences`).
- `monthly`/`weekly-Nx` — как именно сервер раскладывает «дважды/трижды в неделю» по конкретным датам внутри `daysOfWeek` — не проверялось (для задачи «по будням» неважно).

### ✅ Подтверждено вживую (test+revert, состояние возвращено)
- `PUT improvement-jobs/{repoId}/{kind}` — включение/выключение и произвольные `frequency`+`daysOfWeek`.
- `frequency` и `daysOfWeek` независимы; сервер не нормализует.
- `scheduleTimeSource` ∈ {`auto`,`user`}; `manual` → `400 BAD_SCHEDULE_TIME_SOURCE`.
- `PUT .../audits/{actionKey}/email-preferences` с частичным телом; автосохранение по клику.
- Полный enum `frequency` (6 пресетов).
