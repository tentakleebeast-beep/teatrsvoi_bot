# 84. Ревью ошибок API и мёртвый код

> **Статус на 01.11.2026: находки закрыты, отчёт оставлен как основание.** Конверт ошибок
> с кодом и `request_id` — `docs/architecture/85-error-catalog.md` и `services/tool-api/app/errors.py`;
> мёртвый код и декораторы — `scripts/check_deadcode.py` и job `deadcode` в CI;
> отменённое подтверждение крупных операций — `docs/archive/38-approvals.md`.
> Числа и строки файлов ниже — срез на коммите `1efffaa`, не текущее состояние.

Дата: 31.10.2026. Коммит `1efffaa`. Запрос Виктора: провести ревью ошибок API и поиск мёртвого кода.
Всё ниже — снято командами в этом репозитории, не по памяти. Ссылки — на файлы и строки текущего коммита.

Инструменты: `ruff 0.17.3`, `vulture 2.16`, `deadcode 2.4.1` (под Python 3.12: под 3.14 пакет падает
на `ast.Str`), `pytest` по `tests/unit` — 853 теста, 1 падение (см. §4.1).

---

## 0. Главное

1. **Формы ответа при ошибке — четыре разные.** `{"detail": "строка"}`, `{"detail": [список]}`
   (валидация), `{"ok": false, "detail": ...}` (ValueError-обработчик) и `200 {"ok": false, "error": ...}`
   на 15 маршрутах. Клиент не может обработать ошибку одним правилом.
2. **Кода ошибки нет.** 64 разных строки в `detail`, из них 42 в стиле `snake_case`, 9 — английские
   фразы, 8 — русский текст, 5 — f-строки. Каталога нет, контракта нет, клиенты уже показывают
   эти строки человеку как есть (`webapp_page.py:1174`, ~20 мест в гейтвее).
3. **Нет обработчика 5xx и нет идентификатора запроса.** Любое необработанное исключение — дефолтный
   `500 {"detail":"Internal Server Error"}` без кода, без `request_id`, без следа для поддержки.
   `correlation_id` в аудите есть, в HTTP-ответ он не попадает.
4. **Мёртвого кода существенно больше, чем одна функция.** Кроме 18 неиспользуемых имён есть
   **целиком осиротевшая ветка подтверждения крупных сумм**: 3 маршрута, JS-кнопки в Mini App,
   две ветки в гейтвее и таблица `approval_request`, в которую уже никто не пишет.
5. **Гейты сейчас красные:** `ruff check .` — 3 ошибки, `tests/unit` падает на ссылках из-за
   каталога `.worktrees/`. Это не следствие ревью, это состояние `main` (см. §4.1); исправлено
   в этом же проходе.

---

## 1. Часть A. Ошибки API `tool-api`

### 1.1. Что измерено

| Показатель | Значение |
|---|---|
| Строк в `services/tool-api/app/main.py` | 5 675 |
| HTTP-маршрутов (уникальных) | 138 |
| `raise HTTPException` | 86 |
| Статусы | 400 — 41, 403 — 14, 404 — 14, 401 — 7, 422 — 6 (`status.` + 1 сырой `422`), 409 — 3 |
| Уникальных строк `detail` | 64 |
| Маршрутов, возвращающих `200` + `ok: false` | 15 |
| Обработчиков исключений | 2 (`ValueError`, `RequestValidationError`) |
| Обработчик 5xx / middleware `request_id` | нет |
| `responses=` в OpenAPI | ни одного |
| Каталог кодов ошибок | нет |

Хорошая база, которую не надо ломать: 409 уже используется для конфликтов (`slot_taken`,
`event_cancelled`), 422 — для семантики, есть глобальный валидатор входа `_check_input`
(`main.py:275-302`), есть идемпотентные ключи и аудит. Проблема не в статусах, а в том, что
**у ошибки нет машинного лица**.

### 1.2. Четыре формы ответа (это и есть главный дефект)

| Источник | Что вернёт клиент | Пример |
|---|---|---|
| `raise HTTPException(status, "slot_taken")` | `{"detail": "slot_taken"}` | 409 |
| `RequestValidationError` (`main.py:338`) | `{"detail": [{"loc","msg","type"}]}` | 422 |
| `ValueError` (`main.py:327`) | `{"ok": false, "detail": "Некорректное значение: ..."}` | 422 |
| `return {"ok": False, "error": ...}` | `200` + тело об ошибке | 15 маршрутов |

Маршруты с `200` + `ok: false`: `register`, `webapp_auth`, `events_preview`, `profile_intro_parse`,
`check_code`, `attach`, `budget_sheet_retry`, `availability_template_from_week`,
`webapp_template_from_week`, `availability_sheet_mismatches`, `availability_reconcile`,
`assistant_ask`, `calendar_event_create`, `calendar_health`, `calendar_events`.

Следствия:

* один и тот же смысл приходит то статусом, то телом (см. E-05);
* повтор запроса нельзя решить по `retryable` — его нет;
* новому клиенту (n8n, скрипты) нужно знать все четыре формы, а не одну.

### 1.3. Находки

**E-01 — критично. Нет обработчика 5xx и нет `request_id`.**
Необработанное исключение (БД, Google, баг в коде) отдаётся дефолтом Starlette: тело
`{"detail":"Internal Server Error"}`, ни кода, ни корреляции с логом. В `audit_event` поле
`correlation_id` ведётся (`main.py:213`), но в ответ не попадает. Поддержка получает
«не работает» без идентификатора запроса.
*Лечение:* обработчик `Exception` + заголовок `X-Request-ID` на всех ответах + `request_id`
в теле ошибки.

**E-02 — критично. Четыре несовместимые формы (см. 1.2).**
Инвариант «одна ошибка — один конверт» нарушен на уровне всего сервиса.
*Лечение:* один конверт RFC 9457 (§1.5), `detail` сохраняется строкой для текущих клиентов.

**E-03 — критично. `200` с телом ошибки на 15 маршрутах.**
`/webapp/auth` при незарегистрированном человеке отвечает `200 {"ok": false, "error":
"not_registered"}` (`main.py:745`), а `/webapp/me` в той же ситуации — `401` (`main.py:722`).
Страница вынуждена смотреть и `response.ok`, и `body.ok`; часть страниц этого не делает
(`webapp_page.py:816` проверяет оба, `webapp_page.py:1352` — только `body.ok`).
*Лечение:* статус по смыслу, `ok: false` в теле остаётся на переходный период.
*Исправлено 31.10.2026 для `/webapp/auth`:* 401 `not_registered` и 403 `archived`
с человеческим `detail`; остальные 14 маршрутов пока отдают `200` (см. §1.6, PR 4).

**E-04 — критично. Кода ошибки нет, `detail` — свободный текст на трёх языках.**
`bad service token`, `amount must be positive`, `not an admin chat` — английские фразы;
`short_text: нужно хотя бы пару слов` — гибрид; `participant_not_found` — код. Клиенты
показывают это человеку: `webapp_page.py:1174` (`throw new Error(data.detail ...)`) и ~20 мест
в гейтвее (`main.py:735, 1927, 2124, ...`). Пользователь Mini App уже видел `slot_taken`
вместо объяснения.
*Лечение:* `code` (машинный, стабильный) + `title`/`detail` (человеческий, не контрактный).

**E-05 — важно. 401/403/200 для одного и того же условия.**

| Условие | `webapp_actor` (`main.py:706`) | `webapp_auth` (`main.py:734`) |
|---|---|---|
| человека нет в базе | `401 not_registered` | `200 {"ok":false,"error":"not_registered"}` |
| архивный | `403 archived` | `200 {"ok":false,"error":"archived"}` |

*Исправлено 31.10.2026:* `webapp_auth` теперь отдаёт те же 401/403, что и `webapp_actor`
(`code = not_registered` / `archived`), а `detail` — человеческий текст. JS `boot()` читает
`detail` для сообщения человеку.
*Дубли схлопнуты:* `archived` → `participant_archived` (один код на архив во всех трёх
местах), `not_found` в архивации/разархивации → `participant_not_found`.

Плюс `403 participant_inactive` (`main.py:490`) и `403 participant_archived` (`main.py:498`) —
это два разных состояния (ожидает одобрения и архив), коды намеренно разные.

**E-06 — важно. 400 и 422 для одного класса ошибок.**
`bad_month` — 400 (`main.py:1812`), `bad_period` — 422 (`main.py:3387`), `bad_day` — 400
(`main.py:1890`), `participants_empty` — 422 (`main.py:3129` и `4085`), а
`person_not_among_candidates` — 400 (`main.py:1101`). Правило «400 = сломан синтаксис,
422 = синтаксис верный, смысл нет» не выдержано.
*Исправлено 31.10.2026 частично:* `bad_day` и `bad_start` сведены к `bad_date`
(все три — разбор даты), текст стал человеческим. Разделение 400/422 по статусам
оставлено как есть: менять его — отдельное решение по контракту, клиенты уже
различают эти ответы.

**E-07 — важно. Утечка внутреннего текста в ответ.**
`str(exc)` в теле: `main.py:716`, `739` (коды `InitDataError` — терпимо, но бесконтрольно),
`3737`, `3868` (текст `DayCardError`). В 200-телах — `str(e)[:200]` от Google/psycopg:
`4226, 4258, 4324, 4375, 4851, 4882, 4902`. Это внутренние сообщения, их нельзя показывать
пользователю и нельзя считать контрактом.
*Лечение:* наружу — стабильный код, текст исключения — только в лог.
*Исправлено 31.10.2026 для 200-ответов:* `sheet_unavailable`, `sheet_write_failed`,
`llm_unavailable`, `calendar_unavailable` отдают человеческий текст, исключение уходит
в лог. Остались диагностические поля (`mismatched.error`, `sheet_error`, health по
календарям) и текст ошибки провайдера LLM — они не показываются человеку.

**E-08 — важно. Нет сигналов повторяемости.**
`429` есть в одном месте (`register`, `main.py:576`, как `200 ok:false`), `503` не используется
вовсе, `Retry-After` не отдаётся. Клиент не знает, повторять ли запрос.

**E-09 — важно. Ошибки не задокументированы per-endpoint.**
В декораторах нет `responses=`; OpenAPI показывает только 422 от pydantic. Из 86 `raise`
ни один не описан в схеме; каталога нет. Клиент узнаёт об ошибке из продакшена.

**E-10 — средне. Логи и ответы не связаны.**
Гейтвей пишет `log.warning("tool-api %s %s -> %s %s", ..., r.text[:200])`
(`tg-gateway/app/main.py:135`), tool-api не пишет ошибку вовсе. Сопоставить ответ клиента
с записью в логе можно только по времени.

**E-11 — средне. Синонимы и дубли кодов.**
`participant_not_found` × 5, `bad_day` × 2, `bad_month` × 2, `not_found` × 2 (в разных
эндпоинтах — архив/разархивация), `alias_bad_length` × 3, `capability required: ...` × 2,
`participants_empty` в двух формах (с подсказкой и без).

**E-12 — мелочь. Внутренние идентификаторы в тексте для человека.**
`capability required: schedule.read.all`, `admin не выдаётся кодом: только через ADMIN_CHAT_ID`,
`outside_work_day: ... передайте allow_outside_hours=true` — это инструкции разработчику,
показанные пользователю.

### 1.4. Каталог (черновик, 45 кодов вместо 64 строк)

Код — `snake_case`, стабилен. `detail` — человеческий текст, не контракт.

| Статус | Коды |
|---|---|
| 400 | `actor_chat_id_required`, `bad_date`, `bad_day`, `bad_month`, `bad_period`, `bad_time`, `bad_interval`, `bad_time_range`, `bad_hours`, `outside_work_day`, `day_outside_week`, `weekday_duplicated`, `date_range_inverted`, `date_range_too_long`, `unknown_kind`, `unknown_status`, `amount_not_positive`, `alias_bad_length`, `aliases_too_many`, `display_name_empty`, `text_too_short`, `travel_minutes_out_of_range`, `admin_role_not_issuable`, `already_assigned`, `person_not_in_candidates`, `proposal_or_person_required`, `no_card`, `self_archive`, `self_revoke_admin`, `last_admin`, `cannot_revoke_last_role`, `not_archived` |
| 401 | `bad_service_token` (3 места), `no_webapp_token`, `not_registered`, `init_data_invalid` (вместо `str(exc)`) |
| 403 | `unknown_participant`, `participant_inactive`, `participant_archived` (один код вместо двух), `admin_panel_forbidden`, `not_an_admin_chat`, `not_your_event`, `capability_required` (+ `metadata.capability`) |
| 404 | `event_not_found`, `participant_not_found`, `invite_not_found_or_used`, `proposal_not_found`, `feedback_not_found`, `approval_not_found` (уйдёт вместе с веткой, §3.3), `resource_not_found` |
| 409 | `slot_taken`, `event_cancelled` |
| 422 | `feedback_text_empty`, `idempotency_key_required`, `participants_empty`, `category_direction_mismatch`, `unknown_participants`, `validation_failed` (pydantic) |
| 5xx | `internal_error` (500), `upstream_unavailable` (502/503) |

Расхождения с текущими строками фиксируются таблицей соответствия в PR: ни один код не
переиспользуется и не удаляется молча.

### 1.5. Целевой конверт (RFC 9457)

Статус — всегда по смыслу, тело — одно на все случаи, медиатип `application/problem+json`:

```json
{
  "type": "https://svoi.local/errors/slot_taken",
  "title": "Окно уже занято",
  "status": 409,
  "detail": "Окно 12.11, 18:00-20:00 занято другим событием",
  "instance": "/tools/events/assign",
  "code": "slot_taken",
  "retryable": false,
  "request_id": "2b0f7c1e-...",
  "errors": [{"field": "day", "value": "2026-11-12", "constraint": "free"}]
}
```

Переходный период (клиенты уже в проде):

1. `detail` остаётся строкой — гейтвей и JS читают его как сейчас;
2. `ok: false` остаётся в теле там, где был, но статус становится правильным;
3. `code` добавляется первым, затем клиенты переводятся на него, затем тексты переписываются;
4. `title`/`detail` объявляются неконтрактными в каталоге (как `Status.message` у Google).

### 1.6. Порядок внедрения (5 PR, каждый самостоятелен)

| PR | Содержание | Проверка | Статус |
|---|---|---|---|
| 1 | `request_id` middleware + `X-Request-ID` + обработчик `Exception` с `internal_error` | тест: 500 содержит `request_id`; лог содержит тот же id | сделано |
| 2 | Единый `problem(...)` хелпер; `HTTPException`-обработчик переводит `detail` в конверт; `detail` сохраняется строкой | тест на 4 прежние формы: все отдают `code`, `status`, `detail` | сделано |
| 3 | Каталог `docs/architecture/85-error-catalog.md` + тест соответствия | тест: каждый `code` из кода есть в каталоге | сделано |
| 4 | `webapp_auth`/`archived` → статусы; осиротевшая ветка подтверждения удалена | интеграционный тест: `webapp/auth` при незарегистрированном → 401 | сделано |
| 5 | `retryable`/`Retry-After`, `responses=` в OpenAPI, перевод гейтвея на `code` | smoke: 429/503 с заголовком | сделано |

---

## 2. Часть B. Мёртвый код

### 2.1. Метод и отсев ложных срабатываний

`deadcode` выдал **231** сообщение. Из них **202** — функции с декоратором
(`@app.get/@app.post` в tool-api, `@router.message/@router.callback_query` в гейтвее):
инструмент не понимает регистрацию обработчиков, это ложные срабатывания. Осталось **29**
кандидатов, каждый проверен поиском по репозиторию (все файлы `services/`, `scripts/`,
`tests/`, `deploy/`).

`vulture` поверх этого даёт только шум: `cls` в `@classmethod`-валидаторах (8 срабатываний),
поля pydantic-моделей (`roles_note`), атрибуты (`autocommit`, `parse_mode`).

### 2.2. Подтверждено: не используется нигде (18 имён, ~170 строк)

| Место | Что | Строк |
|---|---|---|
| `tg-gateway/app/main.py:221` | `resolve_cascade` — старый детерминированный каскад, заменён n8n-роутером | 20 |
| `tg-gateway/app/main.py:2750` | `resolve_person` | 17 |
| `tool-api/app/main.py:3419` | `parse_availability_text` — разбор текста занятости | 53 |
| `tool-api/app/main.py:1469` | `_notify_capability` — очередь уведомлений по возможности | 19 |
| `tool-api/app/main.py:2577` | `needs_approval` — порог подтверждения | 3 |
| `tool-api/app/main.py:232` | `normalize_key` | 3 |
| `tool-api/app/main.py:2267` | `_search_terms` | 3 |
| `tool-api/app/main.py:4177` | `_a1_parts` | 5 |
| `tool-api/app/main.py:314` | `ALLOWED_ROLES` | — |
| `tool-api/app/main.py:4661` | `OPENROUTER_MODEL` (алиас, который никто не читает) | — |
| `tool-api/app/schedule_grid.py:292` | `slots_to_hours` | 2 |
| `tool-api/app/subject.py:33` | `SUBSET_RE` | — |
| `tool-api/app/subject.py:66` | `_stem` | 9 |
| `tool-api/app/window_policy.py:136` | `late_join_time` | 5 |
| `scripts/build_router_workflow.py:157` | `EMPTY_JS` | — |
| `scripts/calendar_fill_draft.py:33` | `END_OF_DAY` | — |
| `scripts/check_services.py:129` | `count_gateway` | 3 |
| `tool-api/app/main.py:4634-4651` | пустой блок из 18 строк — след удалённых маршрутов занятости | 18 |

**Сделано 31.10.2026:** все 18 имён удалены. Вместе с ними ушли функции, которые вызывались
только из удалённых: `categories_of` и `find_participant` в гейтвее (звались из
`resolve_cascade`/`resolve_person`), `venue_busy` (звался из `parse_availability_text`),
`OPENROUTER_API_KEY` и неиспользуемые импорты `WEEKDAYS`, `column_index`. Пустые блоки схлопнуты.

### 2.3. Используется только тестами (6 имён) — решение продуктовое

`person_key.is_valid`, `slots.longest_window`, `version.format_uptime`,
`gateway.menu_labels`, `routing.is_howto`, `main.venue_busy`.

Это не «мусор»: у каждого есть тесты. Либо это публичная функция модуля, которую надо
использовать (тогда — использовать), либо удалять вместе с тестами. Пока они создают
ложное ощущение покрытого API. Рекомендация: удалить `is_valid` и `format_uptime`
(дублируют валидацию из `webapp.py` и форматирование в шаблоне), остальные — проверить
при следующем касании модуля.

### 2.4. Осиротевшая ветка «подтверждение крупных сумм» — важнее, чем неиспользуемые функции

Решение 30.10.2026 отменило подтверждение крупных сумм (`main.py:2764-2767`), но ветка
осталась целиком и вводит в заблуждение:

| Что осталось | Где |
|---|---|
| `POST /tools/budget/commit` по-прежнему обещает подтверждение в docstring | `main.py:2737-2740` |
| `GET /admin/approvals` — всегда пусто: в `approval_request` никто не пишет | `main.py:2883`, `db/init/020_approvals.sql` |
| `POST /admin/approval/decide` — может ответить только `404 approval_not_found` | `main.py:2891` |
| `POST /webapp/admin/approval` | `main.py:5380` |
| JS `decideApproval` + `approvalButtons` — функция нигде не вызывается | `webapp_page.py:1188-1200` |
| Ветки `if res.get("requires_approval")` в двух сценариях бюджета | `tg-gateway/app/main.py:1636, 1828` |
| `needs_approval`, `_notify_capability` | `main.py:2577, 1469` |
| Таблица `approval_request` + индекс | `db/init/020_approvals.sql` |

Проверено: `insert into approval_request` нет ни в одном файле; интеграционный тест прямо
утверждает, что заявок не создаётся (`tests/integration/test_working_day_and_alerts.py:113`).
То есть три маршрута и кнопки в интерфейсе — мёртвая функциональность, а не мёртвый код.

Рекомендация: удалить маршруты и JS, оставить таблицу до миграции (в схеме есть данные
истории), docstring `budget_commit` привести к факту. Отдельным решением — удалять ли таблицу.

**Сделано 31.10.2026:** удалены `GET /admin/approvals`, `POST /admin/approval/decide`,
`POST /webapp/admin/approval`, `ApprovalIn`, `_approval_items`, `needs_approval`,
`CONFIRM_ABOVE`, `_notify_capability`, JS `decideApproval`/`approvalButtons`, обе ветки
`requires_approval` в гейтвее; docstring `budget_commit` приведён к факту. Таблица
`approval_request` оставлена; `docs/archive/38-approvals.md` помечен как отменённый, FR-G6 в
`docs/development/19` переведён в «отменено», сценарий S-28 переписан.

### 2.5. Неиспользуемые параметры и переменные

* `subject.parse_subject(..., role_hint: str = "")` — параметр документирован, но не
  используется ни в одной ветке (`subject.py:163-169`), вызывающий код его не передаёт
  (`main.py:3097`). Строки 168-169 описания обещают поведение, которого нет: либо передавать
  роль и реализовать ветку, либо убрать параметр и абзац. Остальные «неиспользуемые
  переменные» vulture (`cls`, `roles_note`) — ложные срабатывания (§2.1).

### 2.6. Чего не трогать

* обработчики с декораторами (202 сообщения deadcode) — живые;
* `venue_busy_for` при мёртвом `venue_busy` — первая используется (`main.py:993, 1134, 1398, 3235`);
* `_prompt_cache`, `_user_cache` — читаются и пишутся;
* `db/init/` и `scripts/` — там мёртвое проверять отдельно: часть скриптов одноразовые
  (импорт, сброс), их «неиспользуемость» нормальна.

### 2.7. Отдельный прогон по `scripts/`

`deadcode scripts` — одно сообщение, и то ложное (`autocommit` в `psycopg.connect`).
Реальная находка другая: проверка «упоминается ли скрипт вне самого себя» (docs, deploy,
тесты, launchd, другие скрипты) дала **один осиротевший файл** —
`scripts/replica_finance_sheet.py` (261 строка, коммит `b3b4ec9`). Это WIP-реплика листа
«Финансы 2026 от 07», заменённая рабочей `scripts/copy_finance_sheet.py`; ни одна
инструкция и ни один тест на неё не ссылаются. Удалён 31.10.2026; при необходимости
восстанавливается: `git checkout b3b4ec9 -- scripts/replica_finance_sheet.py`.

Остальные 41 скрипт упоминаются в документации, deploy или тестах — их не трогаем.

---

## 3. Часть C. Гигиена, обнаруженная попутно

### 3.1. Гейты на `main` были красные

* `ruff check .` — 3 ошибки: `B017` (`tests/unit/test_budget_amount_parsing.py:34`),
  `E741` (`tests/unit/test_deploy_scripts.py:275`), `I001` (`tests/webapp/test_miniapp_visual.py:10`).
  CI запускает `ruff check . --output-format=concise` (`.github/workflows/tests.yml:19`) —
  сборка падала бы на любом PR.
* `pytest tests/unit` — 853 теста, 1 падение: `test_docs_links::test_ссылки_живые` находит
  битые ссылки внутри `.worktrees/ux-ui-rework/` — это чужие чекауты в каталоге репозитория,
  тест их не пропускает.

Исправлено в этом проходе (см. §4).

### 3.2. Рабочие деревья

`git worktree list`: `.worktrees/s3-storage` (ветка `13ba221`) и `.worktrees/ux-ui-rework`
(тот же коммит, что `main`). Второе — уже слито; первое — старая ветка хранилища. Пока они
лежат внутри корня, они попадают в поиск файлов, линтер (спасает `.gitignore`) и тесты
документации. Рекомендация: `git worktree remove` для слитого, остальные — вынести за корень.

---

## 4. Что изменено в этом проходе

* [x] `tests/unit/test_budget_amount_parsing.py` — `pytest.raises(ValidationError)` вместо
      `Exception` (B017; проверяется то же поведение, но точнее).
* [x] `tests/unit/test_deploy_scripts.py` — `l` → `row` (E741).
* [x] `tests/webapp/test_miniapp_visual.py` — порядок импортов (I001).
* [x] `tests/unit/test_docs_links.py` — `.worktrees/` исключён из обхода: это не документация проекта.
* [x] этот документ + пересобранный `docs/README.md`.
* [x] `services/tool-api/app/errors.py` — единый конверт ошибок: `code`, `title`, `status`,
      `detail`, `instance`, `retryable`, `request_id`, `ok: false`; медиатип
      `application/problem+json`; `Retry-After` для 429/503.
* [x] `services/tool-api/app/main.py` — middleware `X-Request-ID` (чужой принимается только
      безопасной формы), обработчики `StarletteHTTPException`, `ValueError`,
      `RequestValidationError` и новый обработчик `Exception` (500: клиенту код и
      `request_id`, текст исключения — только в лог). `detail` сохранён строкой/списком,
      поэтому гейтвей и Mini App не сломались.
* [x] `tests/unit/test_api_errors.py` — 15 тестов: коды из текста, конверт 409/503,
      заголовок на успешном ответе, эхо `X-Request-ID`, 422 со списком, 500 без утечки
      текста, 404 в конверте.
* [x] удалена осиротевшая ветка подтверждения крупных сумм (§2.4) и 18 мёртвых имён (§2.2).
* [x] `docs/architecture/85-error-catalog.md` — каталог 66 кодов (60 из `raise` + служебные
      5xx/валидация): HTTP-статус, когда возникает,
      что делать клиенту, какие повторять; конверт и правила переходного периода.
      `tests/unit/test_error_catalog.py` валит сборку, если `raise` даёт код вне каталога
      или статус расходится.
* [x] `ApiError` (наследник `HTTPException` с явным `code`) — для ошибок, где `detail`
      является текстом исключения: `init_data_invalid` (401) и `day_card_invalid` (400)
      вместо `str(exc)` без кода.
* [x] `ReconcileIn` перенесён выше первого использования — `/openapi.json` и `/docs`
      снова собираются (дефект был и в HEAD); тест `test_openapi_собирается`.
* [x] `/webapp/auth`: `200 ok:false` → `401 not_registered` / `403 archived` с человеческим
      `detail`; JS `boot()` показывает `detail`. Интеграционный тест обновлён.
* [x] OpenAPI: схема `Problem` + ответы 400/401/403/404/409/422/429/500/503 у каждого метода
      (через `custom_openapi`, без правки 138 декораторов); тест проверяет 500 и ссылку на схему.
* [x] гейтвей: `error_code()` / `error_text()` — логика ветвится по коду, человеку показывается
      `detail`/`title`, а не `slot_taken`; 5 unit-тестов.
* [x] 200-ответы больше не отдают текст исключения (`sheet_*`, `llm_unavailable`,
      `calendar_unavailable`) — причина уходит в лог.
* [x] `register`: `rate_limited` → `429` с `Retry-After`, `person_key_taken` → `409`
      (оба с человеческим `detail`); коды добавлены в каталог, гейтвей показывает `detail`,
      интеграционный тест обновлён. Диалоговые ответы (`need_name`, `already_registered`,
      `code_*`) намеренно остались `200` — это шаги сценария, а не сбой.
* [x] `assistant_ask`: `quota_exceeded` → `429`, `llm_unavailable` → `503`, `llm_error` → `502`;
      текст провайдера уходит в лог, человеку — понятная причина. Коды в каталоге.
* [x] дубли кодов схлопнуты: `archived` → `participant_archived`, `not_found` в
      архивации/разархивации → `participant_not_found`.
* [x] частичный успех задокументирован: раздел §5 каталога (13 маршрутов) и пометка
      `x-partial-success` в OpenAPI; тест проверяет, что пометка есть у каждого.
* [x] `scripts/`: прогон `deadcode` чист (одно ложное срабатывание); удалён осиротевший
      WIP-скрипт `replica_finance_sheet.py` (§2.7).
* [x] английские `detail` переписаны на русский с явными кодами: `bad_service_token`,
      `actor_chat_id_required`, `unknown_participant`, `participant_inactive`,
      `not_an_admin_chat`, `amount_not_positive`; `bad_day`/`bad_start` → `bad_date`
      с человеческим текстом. Каталог обновлён.
* [x] `docs/archive/38-approvals.md`, `docs/development/19` (FR-G6), `docs/development/20` (S-28) —
      приведены к отменённому решению, чтобы документация не обещала удалённое.

Проверка после правок: `ruff check .` — 0 ошибок; `pytest tests/unit` — 879 пройдено
(853 прежних + 26 новых); `pytest tests/integration` — 337 пройдено на `svoi_test_<pid>`,
0 падений; `/openapi.json` — 200, 130 путей, схема `Problem` и `x-partial-success`;
маршрутов в приложении 147, маршрутов с `approval` — 0.

---

## 5. Приоритеты

| Приоритет | Что | Почему сейчас |
|---|---|---|
| P0 | E-01 (`request_id` + обработчик 500) | без него любой инцидент не расследуется |
| P0 | E-02/E-03 (один конверт, статусы вместо `200 ok:false`) | клиенты уже расходятся в поведении |
| P0 | §2.4 (удалить осиротевшее подтверждение) | обещает пользователю то, чего нет |
| P1 | E-04 + каталог кодов | без кода клиенты привязаны к тексту |
| P1 | E-05/E-06 (401/403, 400/422) | один смысл — один статус |
| P1 | §2.2 (18 имён, ~170 строк) | дешево и снимает шум для инструментов |
| P2 | E-07…E-12, §2.3, §3.2 | качество и гигиена |

---

## 6. Команды воспроизведения

```bash
# линтер и тесты (гейты CI)
env -u PYTHONPATH .venv/bin/ruff check .
env -u PYTHONPATH .venv/bin/python -m pytest tests/unit -q

# мёртвый код: deadcode требует Python <= 3.13 (на 3.14 падает)
uv tool run --python 3.12 --from deadcode deadcode services scripts deploy

# шум по неиспользуемым именам/атрибутам
env -u PYTHONPATH .venv/bin/python -m vulture services scripts deploy --min-confidence 70
```

## Приложение. Предложение по конфигурации

**Применено 01.11.2026 (карта t_b9c9674c).** Предложение ниже записано как было; при
применении три пункта пришлось изменить — проверено прогоном, не на глаз:

1. `min_confidence = 80` вместо 60 — **неверно**: vulture оценивает настоящие мёртвые
   функции в 60%, то есть порог 80 скрывает ровно то, что инструмент должен показывать.
   Рабочий порог — 60, шум снимают декораторы и список ложных имён.
2. Декораторы в vulture пишутся с `@` и допускают шаблон: `"@router.*"`, `"@app.*"`,
   а не перечисление `get/post/delete` — иначе новый маршрут снова станет шумом.
3. `[tool.deadcode]` deadcode не читает: конфигурационных файлов он не понимает вовсе,
   а его флаги `--ignore-*-decorated-with` разбираются и не применяются
   (`DeadCodeVisitor.ignore_decorators` остаётся пустым; на репозитории 289 сообщений
   и с флагами, и без). Поэтому раздел читает `scripts/check_deadcode.py`, он же
   отсеивает обработчики по AST.

На чистом main отчёт после фильтра: **259 обработчиков отсеяно, 26 имён осталось**
(они в `config/deadcode-baseline.txt`: группа 1 — используется только тестами, §2.3;
группа 2 — мёртвый код без обращений вне объявления, чистка отдельной картой).
Новая мёртвая функция красит job `deadcode` в CI и печатается в логе.

```toml
[tool.vulture]
min_confidence = 60
paths = ["services", "scripts", "deploy"]
ignore_decorators = ["@app.*", "@router.*", "@model_validator", "@field_validator",
                     "@property", "@classmethod"]
ignore_names = ["autocommit", "parse_mode", "openapi", "roles_note", "cls"]

[tool.deadcode]
paths = ["services", "scripts", "deploy"]
ignore_decorators = ["@router.*", "@app.*", "@field_validator", "@model_validator"]
ignore_names = ["autocommit", "parse_mode", "openapi", "roles_note", "cls"]
```

Смысл: почти все сообщения — обработчики, зарегистрированные декоратором. Пока это не
настроено, инструмент непригоден для CI: шум съедает сигнал.
