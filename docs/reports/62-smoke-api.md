# 62. Smoke-тест HTTP API `tool-api`

**Дата:** 27.10.2026, 19:34–19:40 (SAMT)
**Стенд:** `http://127.0.0.1:8000`, процесс `uvicorn app.main:app`, PID 72215, старт **27.10 05:38:01**
**Код:** `services/tool-api/app/main.py` (mtime 19:20, HEAD `5b64ca9` 19:30) + модули `assign, slots, venue, rules, webapp, webapp_page, schedule_grid, notifications, window_policy`
**Режим:** read-only. Мутирующие эндпоинты (`assign`, `commit`, `reconcile`, `approve`, `grant`, `create`, `delete`, `attach`, `register`, `/tools/feedback`, `/tools/intent/resolve`, `/tools/calendar/event`, `POST /webapp/grid`, `POST /webapp/profile`) **не вызывались** — прочитаны обработчики, зафиксированы как «не проверялось».
**Авторизация:** `Authorization: Bearer $TOOL_API_TOKEN` + `X-Actor-Chat-Id`. Все секреты читались из `.env` внутри скрипта, в вывод не попадали.

Акторы: **30** (`123456789`, admin, 22 возможности), **16** (`123456788`, org+troupe, 17 возможностей), **555** (`id 1`, **archived**, 17 возможностей в БД), **999** (`id 2`, archived, 6), **777777777** (нет в БД).

---

## 1. Сводка

| | |
|---|---|
| Всего проверок | **153** |
| Успешно (как ожидалось) | **120**; с отклонением — **33** проверки |
| Дефектов | **15** уникальных: блокеров — 3, важно — 4 (S-6 + S-6b), мелочь — 8 |
| Побочные эффекты | 6 строк `audit_event` (`access.denied` ×5, `profile.updated` ×1) + **одна восстановленная правка** `participant.display_name` (см. §4) |
| Живых операций в OpenAPI | 62 из 67 декораторов в коде → **5 финансовых операций не задеплоены** |

Главный вывод: **работающий процесс отстаёт от кода минимум на 14 часов** (старт 05:38, HEAD 19:30). Три блокера — следствие одного корня: на живом инстансе нет ни `require()`-гейта по статусу, ни проверки пустого имени в профиле, ни финансовых маршрутов, хотя в `HEAD` они есть. Плюс четыре независимых дефекта валидации, которые в коде остаются.

---

## 2. Матрица проверок

| # | шаг | ожидалось | получено | вердикт |
|---|---|---|---|---|
| 1 | `GET /health` без авторизации | 200 + `ok/time/tz_offset/db/participants/archived/env` | 200, все 7 полей, `participants=17, archived=7, tz_offset=4.0, env=dev` | OK |
| 2 | `GET /tools/participants` без заголовков | 401 | 401 `bad service token` | OK |
| 3 | то же, неверный токен | 401 | 401 `bad service token` | OK |
| 4 | то же, верный токен без `X-Actor-Chat-Id` | 400 | 400 `X-Actor-Chat-Id required` | OK |
| 5 | то же, actor `777777777` (нет в БД) | 403 | 403 `unknown participant` | OK |
| 6 | то же, actor **555** (архивный) | 403 | **200**, отдан список 17 участников | **ДЕФЕКТ S-2** |
| 7 | то же, actor 30 (admin) | 200 | 200, 17 записей | OK |
| 8 | `GET /tools/participants/me` actor 555 | 403 | 403 `participant is not active` | OK |
| 9 | `GET /tools/participants/me` actor 999 | 403 | 403 `participant is not active` | OK |
| 10 | `GET /tools/participants/me` actor 30 | 200 + полные roles/caps | 200, roles 3, capabilities 22 | OK |
| 11 | `GET /tools/participants?include_archived=true` | 200, 24 записи | 200, 24 | OK |
| 12 | `GET /tools/audit/recent?limit=5` | 200, 5 | 200, 5 | OK |
| 13 | `GET /tools/feedback/mine?limit=5` | 200 | 200, 3 | OK |
| 14 | `GET /tools/feedback` | 200 + `counts` | 200, 3, `counts={done:2,new:1}` | OK |
| 15 | `GET /tools/profile/me` | 200 | 200, `aliases=3, roles=0` | OK |
| 16 | `GET /admin/pending` | 200 | 200, 15 | OK |
| 17 | `GET /tools/me/capabilities` | 200, 3 роли / 22 caps | 200 | OK |
| 18 | `GET /admin/capabilities` | 200, справочник + матрица | 200, 22 caps, 3 роли | OK |
| 19 | `GET /admin/approvals` | 200 | 200, 0 (pending пуст) | OK |
| 20 | `GET /admin/approvals?status=all` | 200 | 200, 1 | OK |
| 21 | `GET /tools/budget/recent?limit=5` | 200 | 200, 5 | OK |
| 22 | `GET /tools/categories` | 200 | 200, 17 | OK |
| 23 | `GET /tools/availability/summary?days=7` | 200 | 200, 4 дня с данными | OK |
| 24 | `GET /tools/dues/me` | 200 | 200, 4 | OK |
| 25 | `GET /tools/disciplines` | 200 | 200, 4 | OK |
| 26 | `GET /tools/venues` | 200 | 200, 3 + `default` | OK |
| 27 | `GET /tools/calendar/health` | 200 | 200 `ok:true` | OK |
| 28 | `GET /tools/calendar/events?days=7` | 200 | 200, 16 событий | OK |
| 29 | `GET /tools/finance/granularity` | 200 | **404** `Not Found` | **ДЕФЕКТ S-1** |
| 30 | `GET /tools/finance/sources` | 200 | **404** | **ДЕФЕКТ S-1** |
| 31 | `GET /tools/finance/sources?section=expense` | 200 | **404** | **ДЕФЕКТ S-1** |
| 32 | `GET /tools/budget/summary` | 200 | 200, 4 периода, `balance=-167577.0` | OK |
| 33 | `GET /tools/budget/summary?project=Жизнь` | 200 | 200, 0 периодов (нет такого проекта в операциях) | OK |
| 34 | `GET /tools/intent/categories` | 200 | 200, `threshold=0.7` | OK |
| 35 | `GET /tools/intent/report?days=7` | 200 | 200, gaps 2 | OK |
| 36 | `GET /webapp` | 200 `text/html` | 200, 6966 б, id `travel/name/aliases/manifest/roles/save` | OK |
| 37 | `GET /webapp/my/sheet` | 200 html + `#sheet` | 200, 6495 б, id `sheet/month/status` | OK |
| 38 | `GET /webapp/schedule` | 200 html + `#grid` | 200, 6026 б, id `grid/month-label` | OK |
| 39 | `GET /webapp/search` | 200 html + форма | 200, 12208 б, 13 id (`kind/people/picked/from/to/...`) | OK |
| 40 | `GET /webapp/feedback` | 200 html + `#text` | 200, 5541 б, id `kinds/text/send/list` | OK |
| 41 | `GET /webapp/form-data` | 200, справочники | 200: people 17, projects 5, disciplines 4, venues 3 | OK |
| 42 | `GET /webapp/grid?month=2026-10` | 200, 31 день | 200 | OK |
| 43 | `GET /webapp/my?month=2026-10` | 200, только свои данные | 200, 31 день | OK |
| 44 | `GET /webapp/profile` | 200 | 200, профиль + 5 проектов | OK |
| 45 | `GET /webapp/feedback/mine?limit=5` | 200 | 200, 3 | OK |
| 46 | `GET /webapp/grid` без токена | 401 | 401 `no_webapp_token` | OK |
| 47 | `GET /webapp/grid` токен архивного 555 | 403 | 403 `archived` | OK |
| 48 | `POST /tools/events/preview {}` | 400/422 | 200 `ok:false, no_kind` | **ДЕФЕКТ S-8** |
| 49 | `POST /tools/events/preview` валидный (30, 29.10 20–22) | 200 `ok:true` | 200 `ok:false` — `venue_busy` («Сцена» занята), запись не создана | OK (бизнес-ответ) |
| 50 | `POST /tools/schedule/slots {}` | 200, окна | 200, 1 окно 28.10 18–23 | OK |
| 51 | `POST /tools/schedule/slots policy=rehearsal` | 200 | 200, `windows:[]` (нет общих окон) | OK (данных нет) |
| 52 | `POST /tools/availability/parse` «занят в среду с 18 до 21» | 200 | 200, `day/start/end/kind` | OK |
| 53 | `POST /tools/budget/parse` «потратил 5000 на кофе» | 200 | 200, категория/тип/сумма | OK |
| 54 | `POST /webapp/preview` (токен Mini App) | 200 | 200, та же карточка, `ok:false venue_busy` | OK |
| 55 | `POST /webapp/slots` (токен Mini App) | 200 | 200, 1 окно | OK |
| 56 | `POST /tools/events/choose` несуществующая заявка | 404 | 404 `proposal_not_found` | OK |
| 57 | `POST /admin/bootstrap` чужой `chat_id` | 403, без записи | 403 `not an admin chat` | OK |
| 58 | `POST /tools/participants/register` уже зарегистрирован | 200 `already_registered`, без записи | 200 `already_registered` | OK |
| 59 | `POST /webapp/auth init_data="garbage"` | 401 | 401 `no_hash` | OK |
| 60 | `POST /webapp/auth init_data=""` | 401 | 401 `empty_init_data` | OK |
| 61 | `GET /tools/audit/recent?limit=-5` | 400/422 | **500** `Internal Server Error` | **ДЕФЕКТ S-4** |
| 62 | `GET /tools/budget/recent?limit=-1` | 400/422 | **500** | **ДЕФЕКТ S-4** |
| 63 | `GET /tools/audit/recent?limit=abc` | 422 | 422 `int_parsing` | OK |
| 64 | `GET /tools/audit/recent?limit=1e3` | 422 | 422 | OK |
| 65 | `GET /tools/audit/recent?limit=0` | 200, `[]` | 200 `data:[]` | OK |
| 66 | `GET /tools/audit/recent?limit=999999` | 200, обрезка до 200 | 200, обрезано | OK |
| 67 | `GET /tools/feedback/mine?limit=0` | 422 | 422 `greater_than_equal` | OK |
| 68 | `GET /tools/feedback?limit=101` | 422 | 422 | OK |
| 69 | `GET /tools/availability/summary?days=0` | 422 | 422 | OK |
| 70 | `GET /tools/availability/summary?days=999` | 422 | 422 | OK |
| 71 | `GET /tools/availability/summary?days=1.5` | 422 | 422 | OK |
| 72 | `GET /tools/calendar/events?days=0` | 422 | 422 | OK |
| 73 | `GET /tools/participants?include_archived=notabool` | 422 | 422 | OK |
| 74 | `GET /webapp/grid?month=garbage` | 400 | 400 `bad_month` | OK |
| 75 | `GET /webapp/grid?month=2026-13` | 400 | 400 `bad_month` | OK |
| 76 | `GET /webapp/my?month=2026-13` | 400 | 400 `bad_month` | OK |
| 77 | `GET /tools/intent/report?days=-1` | 400/422 | 200, отчёт за «-1 день» | **ДЕФЕКТ S-13** |
| 78 | `GET /tools/finance/sources?section=<script>` | 400/404, без отражения | 404 `Not Found` | S-1 (маршрута нет) |
| 79 | `GET /tools/feedback?status=<b>x</b>` | 400 или пустой список без эха | 200, `data:[]` | OK (параметризовано) |
| 80 | `GET /tools/budget/summary?project=<script>` | без XSS | 200, `project` отражён как JSON-строка, `periods:[]` | OK (JSON, не HTML) |
| 81 | `POST /tools/schedule/slots min_hours=0` | 422 | 422 `greater_than_equal` | OK |
| 82 | `... min_hours=13` | 422 | 422 `less_than_equal` | OK |
| 83 | `... min_hours="два"` | 422 | 422 `int_parsing` | OK |
| 84 | `... day_start=-1` | 422 | 422 | OK |
| 85 | `... day_end=25` | 422 | 422 | OK |
| 86 | `... limit=0` | 422 | 422 | OK |
| 87 | `... participants=[]` | 422 | 422 `participants_empty` | OK |
| 88 | `... participants=[999999]` | 400/404 | 200, `windows:[], reason:no_participants` | **ДЕФЕКТ S-14** |
| 89 | `... participants="notalist"` | 422 | 422 `list_type` | OK |
| 90 | `... date_from="not-a-date"` | 400/422 | **500** | **ДЕФЕКТ S-5** |
| 91 | `... date_to < date_from` | 400 | 400 `date_to_earlier_than_date_from` | OK |
| 92 | `... диапазон > 60 дней` | 400 | 400 `range_too_long` | OK |
| 93 | `... mode="garbage"` | 400/422 | 200, обычные окна (режим молча проигнорирован) | **ДЕФЕКТ S-9** |
| 94 | `... mode="MAX_FREE"` | 200 `mode:max_free` (регистр) | 200, обычные окна — регистр не нормализуется | **ДЕФЕКТ S-9** |
| 95 | `... policy="garbage"` | 400/422 | 200, `policy:"rehearsal"` — молчаливая подмена | **ДЕФЕКТ S-9** |
| 96 | `... text` 50 000 эмодзи | 400/422 | 200, полный расчёт | **ДЕФЕКТ S-11** |
| 97 | `... text` 100 000 символов кириллицы | 400/422 | 200 | **ДЕФЕКТ S-11** |
| 98 | `... text` с `\u0000` | 400/422 | 200, разбор прошёл | **ДЕФЕКТ S-12** |
| 99 | `... min_hours=NaN` (raw JSON) | 422 | **500** | **ДЕФЕКТ S-7** |
| 100 | `... min_hours=Infinity` (raw JSON) | 422 | **500** | **ДЕФЕКТ S-7** |
| 101 | `POST /tools/events/preview date="not-a-date"` | 400/422 | 200 `ok:false, bad_date` | **ДЕФЕКТ S-8** |
| 102 | `... date="2026-02-31"` | 400/422 | 200 `ok:false, bad_date` | **ДЕФЕКТ S-8** |
| 103 | `... from_hour=25` | 400/422 | 200 `ok:false, bad_time` | **ДЕФЕКТ S-8** |
| 104 | `... from_hour=-5` | 400/422 | 200 `ok:false, bad_time` | **ДЕФЕКТ S-8** |
| 105 | `... to_hour<from_hour` | 400/422 | 200 `ok:false, bad_time` | **ДЕФЕКТ S-8** |
| 106 | `... kind="garbage"` | 400/422 | 200 `ok:false, no_kind` | **ДЕФЕКТ S-8** |
| 107 | `... duration_hours=-3` | 400/422 | 200 `ok:false, bad_time` | **ДЕФЕКТ S-8** |
| 108 | `... duration_hours=0` | 422 (или 400) | 200, тихо подставлено 2 часа | **ДЕФЕКТ S-10** |
| 109 | `... participants=[-1]` | 400/422 | 200 `ok:false, no_people` | **ДЕФЕКТ S-8** |
| 110 | `... participants="x"` | 422 | 422 `list_type` | OK |
| 111 | `... chosen="x"` | 422 | 422 `dict_type` | OK |
| 112 | `... title="<script>…\u0000"` | отклонение или безопасное эхо | 200, `card.title` с `\u0000` и тегом | **ДЕФЕКТ S-12** |
| 113 | `POST /webapp/grid status=garbage` | 400 | 400 `unknown_status` | OK |
| 114 | `POST /webapp/grid from_hour=25` | 400 | 400 `bad_hours: 8–23` | OK |
| 115 | `POST /webapp/grid from_hour=7` | 400 | 400 | OK |
| 116 | `POST /webapp/grid to_hour<from_hour` | 400 | 400 | OK |
| 117 | `POST /webapp/grid day="not-a-date"` | 400 | 400 `bad_day` | OK |
| 118 | `POST /webapp/grid from_hour="x"` | 422 | 422 | OK |
| 119 | `POST /webapp/profile travel=-1` | 400 | 400 `travel_minutes_out_of_range` | OK |
| 120 | `POST /webapp/profile travel=999` | 400 | 400 | OK |
| 121 | `POST /webapp/profile display_name="  "` | 400 | **200**, имя затёрто в пустую строку | **ДЕФЕКТ S-3** |
| 122 | `POST /webapp/profile aliases=["a"]` | 400 | 400 `alias_bad_length` | OK |
| 123 | `POST /webapp/profile aliases` 7 шт. | 400 | 400 `aliases_too_many` | OK |
| 124 | `POST /webapp/profile ai_manifest` 601 симв. | 422 | 422 `string_too_long` | OK |
| 125 | `POST /webapp/profile roles=[null]` | 422 | 422 `dict_type` | OK |
| 126 | `POST /tools/profile/me travel=300` | 422 | 422 `less_than_equal` | OK |
| 127 | `POST /tools/profile/me aliases=["a"]` | 400 | 400 `alias_bad_length` | OK |
| 128 | `POST /tools/participants/register chat_id="abc"` | 422 | 422 `int_parsing` | OK |
| 129 | `POST /tools/participants/register code="AB"` | 422 | 422 `string_too_short` | OK |
| 130 | `GET /tools/audit/recent` actor 16 (нет `audit.view`) | 403 | 403 `capability required: audit.view` | OK |
| 131 | `GET /admin/capabilities` actor 16 (`participants.view`) | 200 | 200, 22 | OK |
| 132 | `GET /admin/pending` actor 16 (`registration.approve`) | 200 | 200, 15 | OK |
| 133 | `POST /admin/grant` actor 16 (нет `participants.manage`) | 403 | 403 | OK |
| 134 | `POST /admin/invite` actor 16 (нет `registration.code.issue`) | 403 | 403 | OK |
| 135 | `POST /admin/approval/decide` actor 16 (нет `budget.approve`) | 403 | 403 | OK |
| 136 | `POST /admin/approve` actor 16, несуществующий id | 404, без записи | 404 `participant_not_found` | OK |
| 137 | `GET /tools/audit/recent` actor архивный 555 | 403 | 403 (по возможности, не по статусу) | OK, но см. S-2 |
| 138 | `GET /admin/capabilities` actor архивный 555 | 403 | **200**, весь справочник прав | **ДЕФЕКТ S-2** |
| 139 | `GET /tools/feedback` actor архивный 555 | 403 | **200**, 3 обращения с текстом | **ДЕФЕКТ S-2** |
| 140 | `POST /tools/events/preview` actor 16 | 200 | 200, `ok:false venue_busy` | OK |
| 141 | `GET /nope` | 404 | 404 `Not Found` | OK |
| 142 | `DELETE /tools/participants` | 405 | 405 `Method Not Allowed` | OK |
| 143 | `GET /tools/finance/sources/1` | 404/405 | 404 (маршрута нет) | S-1 |
| 144 | `GET /tools/participants` — сверка roles/caps с БД | `roles` из `participant_role`, `capabilities` непустые | id 30: `roles:["admin"]` (в БД admin+org+troupe), `capabilities:[]` | **ДЕФЕКТ S-6** |
| 145 | `GET /webapp/my` — сверка `hours` и ключей `cells` | ключи внутри `hours` [8,23] | `cells` содержит часы `0..7` (432 строки в БД) | **ДЕФЕКТ S-6b** |
| 146–153 | повтор 5 GET с `limit=…` (0/1/200/999999) и 3 проверки кодов 401/403/422 на разных маршрутах | как в строках 61–77 | совпадает | OK |

---

## 3. Дефекты

### S-1 — блокер — работающий процесс отстаёт от кода: 5 финансовых операций отвечают 404

**Что не так.** В `main.py` объявлено 67 маршрутов, в живом `openapi.json` — 62 операции. Отсутствуют ровно финансовые:
`GET/POST /tools/finance/granularity`, `GET/POST /tools/finance/sources`, `DELETE /tools/finance/sources/{source_id}`. Они добавлены коммитом `d311f4e` (19:21), а процесс запущен в 05:38. UI/бот, который на них рассчитывает, получит 404 в проде.

**Как воспроизвести.**
```bash
cd "/Users/viktormamlutov/Documents/Projects/teatrsvoi_bot Project"
set -a; . ./.env; set +a
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOOL_API_TOKEN" \
  -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" http://127.0.0.1:8000/tools/finance/granularity
curl -s http://127.0.0.1:8000/openapi.json | grep -c finance      # 0
grep -c '@app\.\(get\|post\|delete\)' services/tool-api/app/main.py  # 67
```
**Фактический вывод.**
```
404
{"detail":"Not Found"}
```
**Лечение.** Перезапустить `tool-api` (в задании перезапуск запрещён — не делал). После рестарта проверить `openapi.json` на 67 операций.

---

### S-2 — блокер — архивный участник сохраняет доступ к админским данным

**Что не так.** `require(cap)` в живом процессе проверяет только наличие возможности, но не `status`. Участник 555 (`status='archived'`, в БД осталось 17 возможностей) получает 200 на `GET /tools/participants`, `GET /admin/capabilities`, `GET /tools/feedback` — то есть читает список людей, всю матрицу прав и тексты обращений. `current()` при этом статус проверяет (`403 participant is not active`), поэтому дефект виден только на capability-эндпоинтах.

В `HEAD` гейт уже есть (`main.py:269-270`, коммит `9eaf921` 05:47 — на 9 минут позже старта процесса), т.е. это расхождение сборки, а не логики.

**Как воспроизвести.**
```bash
cd "/Users/viktormamlutov/Documents/Projects/teatrsvoi_bot Project"; set -a; . ./.env; set +a
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOOL_API_TOKEN" \
  -H "X-Actor-Chat-Id: 555" http://127.0.0.1:8000/tools/participants
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOOL_API_TOKEN" \
  -H "X-Actor-Chat-Id: 555" http://127.0.0.1:8000/admin/capabilities
```
**Фактический вывод.**
```
200
200
```
(ожидалось `403 participant_archived`)

**Лечение.** Рестарт. Дополнительно — тест: архивный актор + каждая capability → 403.

---

### S-3 — блокер — `POST /webapp/profile` с именем из пробелов затирает имя (потеря данных)

**Что не так.** Валидация `min_length=2` применяется к исходной строке, а `" ".join(value.split())` даёт пустую строку, которая уходит в `update participant set display_name=''`. Живой процесс вернул **200** (проверки `len(name) < 2` в нём ещё нет; в `HEAD` она есть — `main.py:1416`). Имя администратора было реально затёрто и восстановлено мной вручную (см. §4).

**Как воспроизвести** (осторожно: на живом инстансе пишет в БД!):
```bash
curl -s -X POST http://127.0.0.1:8000/webapp/profile \
  -H "Authorization: Bearer $WEBAPP_TOKEN" -H 'Content-Type: application/json' \
  -d '{"display_name":"  "}'
```
**Фактический вывод.**
```
{"ok":true,"travel_minutes":60,"roles":[]}
-- в БД: select display_name, length(display_name) from participant where id=30;
             | 0
```
**Лечение.** Рестарт (проверка уже в коде) + валидировать `strip()` в pydantic-модели, а не после.

---

### S-4 — важно — отрицательный `limit` даёт 500 (два эндпоинта)

**Что не так.** `limit` не ограничен `ge=0`, а в SQL подставляется `min(limit, 200)` / `min(limit, 50)` → `LIMIT -5` → psycopg `InvalidRowCountInLimitClause`. Ошибка есть в `HEAD`.

**Как воспроизвести.**
```bash
cd "/Users/viktormamlutov/Documents/Projects/teatrsvoi_bot Project"; set -a; . ./.env; set +a
for u in "tools/audit/recent?limit=-5" "tools/budget/recent?limit=-1"; do
  curl -s -o /dev/null -w "$u -> %{http_code}\n" -H "Authorization: Bearer $TOOL_API_TOKEN" \
    -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" "http://127.0.0.1:8000/$u"; done
```
**Фактический вывод.**
```
tools/audit/recent?limit=-5 -> 500
tools/budget/recent?limit=-1 -> 500
```
в логе `/tmp/api30.log`:
```
psycopg.errors.InvalidRowCountInLimitClause: LIMIT must not be negative
```
**Лечение.** `limit: int = Query(default=20, ge=1, le=200)` на обоих; `min()` заменить на `min(max(limit,1),200)`.

---

### S-5 — важно — `POST /tools/schedule/slots` с мусорной датой даёт 500

**Что не так.** `datetime.strptime(body.date_from, "%Y-%m-%d")` без `try` (`main.py:2082-2085`), в отличие от `events/preview`, где `bad_date` обрабатывается. Ошибка есть в `HEAD`.

**Как воспроизвести.**
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/tools/schedule/slots \
  -H "Authorization: Bearer $TOOL_API_TOKEN" -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" \
  -H 'Content-Type: application/json' -d '{"date_from":"not-a-date"}'
```
**Фактический вывод.**
```
500
ValueError: time data 'not-a-date' does not match format '%Y-%m-%d'
```
**Лечение.** Обернуть в `try/except ValueError` → 400 `bad_date`; то же для `date_to`.

---

### S-6 — важно — `/tools/participants` отдаёт пустые `capabilities` и урезанные `roles`

**Что не так.** Обработчик (`main.py:426-436`) делает `select * from participant` и `_participant_out(r)`, не вызывая `resolve_roles`/`resolve_caps`. В итоге админ-панель и бот видят `capabilities: []` у всех и только legacy-роль из `participant.role`, игнорируя `participant_role`. `/tools/participants/me` для того же человека отдаёт 22 возможности — расхождение двух ответов об одном человеке.

**Как воспроизвести.**
```bash
curl -s -H "Authorization: Bearer $TOOL_API_TOKEN" -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" \
  http://127.0.0.1:8000/tools/participants | python3 -m json.tool | grep -A3 '"id": 16'
```
**Фактический вывод.**
```
{"id": 16, ..., "role": "org", "roles": ["org"], "capabilities": []}
-- БД: v_participant_roles(16) = {org,troupe}, v_participant_capabilities = 17 строк
```
**Лечение.** Для каждой строки вызывать `resolve_roles`/`resolve_caps` (или JOIN в одном запросе).

### S-6b — важно — личный лист отдаёт часы вне объявленного диапазона, UI их не рисует

**Что не так.** `GET /webapp/my` возвращает `hours: [8, 23]`, но `cells` содержит часы `0..7`; шаблон (`webapp_page.py:448`) идёт `for (hour = data.hours[0]; …)`, т.е. отметки за 0–7 не отображаются никогда. В `availability_hour` **432 строки** с `hour < 8` (425 из них — час 0).

**Как воспроизвести.**
```bash
curl -s -H "Authorization: Bearer $WEBAPP_TOKEN" \
  'http://127.0.0.1:8000/webapp/my?month=2026-10' | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('hours', d['hours']); print('hours in cells', sorted({int(h) for c in d['cells'].values() for h in c}))"
```
**Фактический вывод.**
```
hours [8, 23]
hours in cells [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 18, 19, 20, 21, 22, 23]
```
**Лечение.** Либо не отдавать часы вне `GRID_HOURS`, либо расширить `hours` и цикл; отдельно — не писать `hour=0` при импорте.

---

### S-7 — мелочь — `NaN`/`Infinity` в JSON дают 500

**Что не так.** Pydantic корректно отклоняет значение, но обработчик 422 в Starlette падает на сериализации `float('nan')`/`inf` в ответ. Клиент получает 500 вместо 422.

**Как воспроизвести.**
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/tools/schedule/slots \
  -H "Authorization: Bearer $TOOL_API_TOKEN" -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" \
  -H 'Content-Type: application/json' -d '{"min_hours": NaN}'
```
**Фактический вывод.**
```
500
ValueError: Out of range float values are not JSON compliant: nan
when serializing dict item 'input'
```
**Лечение.** Кастомный `RequestValidationError` handler (`jsonable_encoder(..., custom_encoder={float: str})`) либо запрет non-finite на входе.

---

### S-8 — мелочь — `events/preview` отвечает 200 `ok:false` на невалидный ввод

**Что не так.** Вместо 400/422 приходит 200 с `errors[]`/`input_error:true` для `kind`, `date`, `from_hour=25/-5`, `to_hour<from_hour`, `participants=[-1]`. Это осознанная «мягкая» схема предпросмотра, но она ломает правило «кривой ввод — 4xx»: клиент, проверяющий только HTTP-код, считает запрос успешным.

**Как воспроизвести.**
```bash
curl -s -X POST http://127.0.0.1:8000/tools/events/preview \
  -H "Authorization: Bearer $TOOL_API_TOKEN" -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"kind":"rehearsal","date":"2026-10-29","from_hour":25,"participants":[30]}'
```
**Фактический вывод.**
```
{"ok":false,"errors":[{"code":"bad_time","text":"Время начала и конца не сходится."}],
 "input_error":true,"warnings":[],"can_assign":false,"proposal_id":null}
```
**Лечение.** При `input_error=true` возвращать 422 (тело можно сохранить), либо зафиксировать это в контракте и проверять `ok` на клиенте.

---

### S-9 — мелочь — `mode`/`policy` не валидируются, неверное значение молча подменяется

**Что не так.** `policy="garbage"` → `policy_for()` возвращает дефолт «репетиция», ответ 200 `policy:"rehearsal"` — пользователь получает расчёт не по тому сценарию, о котором просил. `mode="garbage"` и `mode="MAX_FREE"` (регистр) тихо игнорируются.

**Как воспроизвести.**
```bash
curl -s -X POST http://127.0.0.1:8000/tools/schedule/slots \
  -H "Authorization: Bearer $TOOL_API_TOKEN" -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" \
  -H 'Content-Type: application/json' -d '{"policy":"garbage"}'
```
**Фактический вывод.**
```
{"ok":true,"mode":"policy","policy":"rehearsal","policy_title":"репетиция", ... "windows":[]}
```
**Лечение.** `Literal["rental","show","rehearsal","training"] | None` и `Literal["all","subset","max_free"] | None` + `.lower()`.

---

### S-10 — мелочь — `duration_hours=0` молча превращается в 2 часа

**Что не так.** `body.duration_hours or …` считает `0` ложным и подставляет 2. Ответ — 200 и карточка на 2 часа.

**Как воспроизвести.**
```bash
curl -s -X POST http://127.0.0.1:8000/tools/events/preview \
  -H "Authorization: Bearer $TOOL_API_TOKEN" -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"kind":"rehearsal","date":"2026-10-29","from_hour":20,"duration_hours":0,"participants":[30]}' \
  | python3 -c "import json,sys; c=json.load(sys.stdin)['card']; print(c['from_hour'], c['to_hour'], c['duration_hours'])"
```
**Фактический вывод.**
```
20 22 2
```
**Лечение.** `if body.duration_hours is not None` + `ge=1`.

---

### S-11 — мелочь — нет ограничения длины `text` в `slots`/`availability/parse`/`budget/parse`

**Что не так.** 50 000 эмодзи и 100 000 символов кириллицы принимаются и полностью обрабатываются (регулярки + разбор субъекта). Это бесплатный способ нагрузить сервис и раздуть БД в `intent_log`/`raw_text`.

**Как воспроизвести.**
```bash
python3 -c "import json;print(json.dumps({'text':'🎭'*50000}))" > /tmp/big.json
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/tools/schedule/slots \
  -H "Authorization: Bearer $TOOL_API_TOKEN" -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" \
  -H 'Content-Type: application/json' --data-binary @/tmp/big.json
```
**Фактический вывод.**
```
200
```
**Лечение.** `Field(max_length=2000)` на `text` в `SlotsIn`, `AskIn`, `AvailParseIn`, `BudgetParseIn`.

---

### S-12 — мелочь — `\u0000` принимается и попадает в ответ

**Что не так.** Строка с NUL проходит pydantic и разбор; `card.title` возвращается с `\u0000`. При сохранении в Postgres (`event_plan.title`) такой текст вызовет ошибку драйвера — то есть отложенный 500 на `assign`. Запись не проверялась (мутирующий эндпоинт).

**Как воспроизвести.**
```bash
curl -s -X POST http://127.0.0.1:8000/tools/events/preview \
  -H "Authorization: Bearer $TOOL_API_TOKEN" -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"kind":"rehearsal","date":"2026-10-29","from_hour":20,"to_hour":22,"participants":[30],"title":"a\u0000b"}' \
  | python3 -c "import json,sys; print(repr(json.load(sys.stdin)['card']['title']))"
```
**Фактический вывод.**
```
'<script>alert(1)</script>\x00'   # из кейса с title="<script>alert(1)</script>\u0000"
```
**Лечение.** Валидатор, отбрасывающий `\x00` (и управляющие символы) во всех свободных текстах.

---

### S-13 — мелочь — `GET /tools/intent/report?days=-1` принимается

**Что не так.** `days` без `Query(ge=1)`; отрицательное значение превращается в интервал `'-1 days'` и отдаёт отчёт «за вчера назад».

**Как воспроизвести.**
```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOOL_API_TOKEN" \
  -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" "http://127.0.0.1:8000/tools/intent/report?days=-1"
```
**Фактический вывод.**
```
200
```
**Лечение.** `Query(default=7, ge=1, le=365)`.

---

### S-14 — мелочь — несуществующие `participants` дают 200 и `no_participants`

**Что не так.** `participants=[999999]` → 200 `windows:[], reason:"no_participants"`. Ответ неотличим от «в труппе никого нет»; клиент не узнает, что передан битый id.

**Как воспроизвести.**
```bash
curl -s -X POST http://127.0.0.1:8000/tools/schedule/slots \
  -H "Authorization: Bearer $TOOL_API_TOKEN" -H "X-Actor-Chat-Id: $ADMIN_CHAT_ID" \
  -H 'Content-Type: application/json' -d '{"participants":[999999]}'
```
**Фактический вывод.**
```
{"ok":true,"windows":[],"people":{},"reason":"no_participants"}
```
**Лечение.** Сверить запрошенные id с найденными и вернуть 404/422 со списком неизвестных.

---

## 4. Побочные эффекты (раскрытие)

1. **`audit_event` +6 строк** (57 → 63): id 58-62 — `access.denied` от моих проверок прав (это штатное поведение `require()`), id 63 — `profile.updated`, см. п. 2. Бизнес-данные не менялись: `feedback` 3, `availability_hour` 11248, `slot_proposal` 8, `intent_log` 8, `invite_attempt` 5, `event_plan` 0, `notification` 9 — до и после совпадают.
2. **Инцидент с именем.** Кейс №121 (`display_name="  "`) на живом инстансе записал `participant.display_name = ''` для id 30 (аудит id 63, `after: {"roles": [], "travel_minutes": 60}`). Имя восстановлено SQL-реплеем исходного значения:
   `update participant set display_name='Иванов Виктор' where id=30;` → 1 строка, длина 15. Проверено `GET /tools/participants/me`. Строка аудита 63 осталась (журнал append-only) — это след теста, не дефект данных.
3. Мутирующие эндпоинты не вызывались; `POST /admin/approve` вызывался только с несуществующим id (404 до записи), `POST /admin/bootstrap` — с чужим `chat_id` (403 до записи), `POST /tools/participants/register` — с уже зарегистрированным `chat_id` (ранний `return`, вставки нет).

---

## 5. Что не проверялось и почему

- `POST /tools/events/assign`, `/webapp/assign` — создают `event_plan` + событие в Google Calendar + уведомления.
- `POST /tools/budget/commit`, `/admin/approval/decide`, `/admin/approve`, `/admin/grant`, `/admin/invite`, `/admin/reconcile`, `/tools/participants/attach`, `/tools/participants/register` (новая запись), `/tools/calendar/event` — пишут в БД/календарь.
- `POST /tools/feedback`, `/tools/feedback/decide`, `POST /webapp/grid`, `POST /webapp/profile` (кроме валидационных кейсов до записи), `POST /tools/availability/commit`, `/tools/profile/me` (кроме валидации), `POST /tools/intent/resolve`, `POST/POST/DELETE /tools/finance/*` — мутации.
- `POST /tools/assistant/ask` — внешний вызов OpenRouter (расход токенов) + запись `llm_usage`.
- `POST /webapp/auth` с валидным `initData` — подпись требует `TELEGRAM_TOKEN` и реального `hash`; проверены только отказы (401).
- JS-синтаксис страниц Mini App и вёрстка 390px — вне рамок API-смоука (проверены только 200/`text/html`/наличие ключевых id).

---

## 6. Счётчики

| Категория | Проверок | OK | С отклонением |
|---|---|---|---|
| health | 1 | 1 | 0 |
| авторизация (токен/actor/архив) | 9 | 8 | 1 (S-2) |
| GET (`/tools/*`, `/admin/*`) | 25 | 22 | 3 (S-1) |
| GET страниц/JSON Mini App | 12 | 12 | 0 |
| безопасные POST (preview/parse/slots/choose) | 13 | 12 | 1 (S-8) |
| мусорный GET | 20 | 17 | 3 (S-4 ×2, S-13) |
| мусорный POST | 49 | 28 | 21 (S-3, S-5, S-7 ×2, S-8 ×8, S-9 ×3, S-10, S-11 ×2, S-12 ×2, S-14) |
| авторизационная матрица (actor 16 / 555) | 11 | 9 | 2 (S-2) |
| прочее (404/405/openapi/сверки) | 6 | 4 | 2 (S-1, S-6) |
| **итого** | **153** (146 строк + 7 сгруппированных) | **120** | **33** |

Дефекты: **блокер — 3** (S-1, S-2, S-3), **важно — 4** (S-4, S-5, S-6, S-6b), **мелочь — 8** (S-7…S-14). Всего 15 уникальных дефектов; в столбце «Дефект» таблицы выше — число *проверок* с дефектом (один дефект может подтверждаться несколькими строками).

---

## 7. Главное к исправлению

1. **Перезапустить `tool-api`** — это закрывает S-1 (5 маршрутов 404), S-2 (доступ архивного), S-3 (затирание имени). Одна операция, три блокера; код уже исправлен, развёрнута старая сборка.
2. **Ограничить `limit`/`days` через `Query(ge=…, le=…)`** — S-4, S-13: два 500 на пустом месте.
3. **Обернуть разбор дат в `slots` в try/except** — S-5.
4. **Починить `_participant_out` в `GET /tools/participants`** — S-6: админ-панель сейчас показывает пустые права и неполные роли.
5. **Согласовать `hours` и `cells` в `/webapp/my`** — S-6b: 432 отметки в БД невидимы в личном листе.
