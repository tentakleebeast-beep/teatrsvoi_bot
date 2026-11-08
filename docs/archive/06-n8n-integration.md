> **Архив.** Заменён: `docs/development/108-agent-mode.md` (роутер свободного текста `POST /tools/route`), контур — `docs/architecture/30-architecture-c4.md`. Причина: n8n выведен из контура целиком (V-026, 01.11.2026), описанных здесь воркфлоу и вебхуков больше нет.

# 06. Интеграция с n8n: контракты и воркфлоу

> **Выведено из контура (V-026, 01.11.2026).** Решение заказчика: «убрать целиком».
> Контейнер n8n, публичный адрес `n8n.<домен>` и переменные `N8N_*` удалены из деплоя.
> Роутер свободного текста живёт в tool-api: `POST /tools/route`
> (`services/tool-api/app/router_llm.py`), промпт и правила перенесены из WF-01.
> Документ оставлен как история контрактов — по нему делался перенос маршрутизации.

n8n был оркестратором пользовательских сценариев. Перенос не требовал правок в боте — только `N8N_BASE_URL` и секрет.

---

## 1. Границы

n8n делал: маршрутизацию интентов, вызов LLM, извлечение слотов, формулировки, диалоговые ветки, композицию ответа.
n8n **не делал**: доступ к Postgres, доступ к Google API, права, аудит, идемпотентность, отправку в Telegram.

Сейчас маршрутизацию и ответ моделью делает tool-api (`POST /tools/route`), и правило
осталось тем же: один вход к данным — один набор инвариантов.

---

## 2. Схема вызова (историческая)

```
Telegram → tg-gateway → POST n8n /webhook/tg-inbound
                              ↓ (LLM + tools)
                        HTTP Request → tool-api (HMAC)
                              ↓
                        {reply_text, buttons, actions}
                              ↓
              tg-gateway → Telegram
```

Сегодняшняя схема короче на один шаг: `tg-gateway → POST tool-api /tools/route → LLM`,
а решения о данных по-прежнему принимает только tool-api.

### 2.1 Запрос бот → n8n (выведено из контура, V-026)

`POST {N8N_BASE_URL}/webhook/tg-inbound`
Заголовки: `X-Signature: hmac_sha256(body, N8N_SHARED_SECRET)`, `X-Timestamp`, `X-Correlation-Id`.

```json
{
  "correlation_id": "0f3a…",
  "participant": {
    "id": 5, "chat_id": 123456789, "role": "org",
    "display_name": "Виктор", "person_key": "иванов",
    "allowed_tools": ["notes:*","budget:write","budget:read","schedule:read_all",
                      "schedule:write","availability:self","calendar:write"]
  },
  "session": {"state": null, "payload": {}},
  "text": "расход 3000 на фотосъёмку Зайцева",
  "attachments": [{"type":"document","file_id":"BQAC…","name":"роль.docx"}],
  "recent": [{"role":"user","text":"…"},{"role":"assistant","text":"…"}],
  "now": "2026-10-25T14:02:11+03:00"
}
```

### 2.2 Ответ n8n → бот

```json
{
  "reply_text": "Записываю расход…",
  "parse_mode": "HTML",
  "buttons": [
    {"type":"callback","text":"Записать","data":"budget:confirm:tmp_881"},
    {"type":"callback","text":"Отмена","data":"budget:cancel:tmp_881"}
  ],
  "actions": [
    {"type":"tool_result_summary","tool_call_ids":[8871,8872]},
    {"type":"set_session_state","state":"await_budget_confirm","payload":{"tmp_id":"tmp_881"}}
  ],
  "diagnostics": {"intent":"budget.capture","model":"…","tokens":1420,"ms":2380}
}
```

Бот исполняет `actions` сам. n8n не может «попросить бота» сделать что-то, чего нет в списке типов действий (`send_message`, `edit_message`, `set_session_state`, `schedule_notification`, `request_confirm`).

### 2.3 Ошибка / таймаут
- `5xx` или > 25 с → бот отправляет «Секунду, обрабатываю…», задачу в `dead_letter`, ответ придёт через `/notify`.
- `4xx` с телом `{"error":"…"}` → лог + пользователю нейтральный текст, детали в аудит.

---

## 3. tool-api: эндпоинты для n8n

Все — `POST`, `Authorization: Bearer $TOOL_API_TOKEN`, тело содержит `participant_id` и `correlation_id` (бот подставляет их сам, доверять телу нельзя — сверяет с подписью).

| Эндпоинт | Scope | Назначение |
|---|---|---|
| `/tools/participants/resolve` | `*` | имя/алиас → participant |
| `/tools/participants/me` | `*` | профиль, роль, права |
| `/tools/notes/search` | `notes:read` | поиск по своим заметкам |
| `/tools/notes/upsert` | `notes:write` | создать/изменить заметку (+.md в Drive) |
| `/tools/files/list` | `files:read` | свои файлы / общие |
| `/tools/files/get` | `files:read` | текст файла (для анализа) |
| `/tools/files/save_from_tg` | `files:write` | сохранить вложение в личную папку |
| `/tools/budget/parse` | `budget:write` | текст → черновик транзакции (по `budget-rules.yaml`) |
| `/tools/budget/commit` | `budget:write` | подтверждённый черновик → Sheets + БД + аудит |
| `/tools/budget/query` | `budget:read` | агрегаты, выборки, суммы |
| `/tools/budget/recent` | `budget:read` | последние N операций |
| `/tools/budget/summary` | `budget:read` | значения из листа `Свод` (только чтение) |
| `/tools/dues/me` | `dues:self` | мои начисления и оплаты |
| `/tools/dues/all` | `dues:all` | задолженности по всем |
| `/tools/availability/upsert` | `availability:self` | записать занятость |
| `/tools/availability/summary` | `schedule:read_all` | сводка по дням, покрытие |
| `/tools/schedule/slots` | `schedule:write` | поиск окон (детерминированный) |
| `/tools/schedule/events` | `schedule:read*` | события за период |
| `/tools/calendar/create` | `calendar:write` | создать событие (после подтверждения) |
| `/tools/notify` | `notify:send` | поставить уведомление в очередь |
| `/tools/audit/recent` | `audit:read` | последние записи (admin) |

Ответ всегда: `{"ok":true,"data":…,"source":{…}}` либо `{"ok":false,"error":{"code":"…","message":"…"}}`.

---

## 4. Воркфлоу

| ID | Имя | Триггер | Что делает |
|---|---|---|---|
| WF-00 | `router` | webhook | нормализация, выбор сценария по интенту + роль; fallback → WF-09 |
| WF-01 | `registration` | state=`pending` | подсказки по коду, приём имени, выдача прав |
| WF-02 | `notes` | текст про заметку/файл | search/upsert, анализ документа |
| WF-03 | `budget-capture` | «расход/потратили/оплатили…» | parse → черновик → подтверждение → commit |
| WF-04 | `budget-query` | вопрос о деньгах | query/summary/recent → ответ с источником |
| WF-05 | `dues` | «взнос», «сколько я должен» | dues/me, dues/all, отметка оплаты |
| WF-06 | `availability-intake` | «занят/могу/свободен…» | парсер дат, черновик, upsert |
| WF-07 | `schedule-slots` | «когда можем собраться / предложи окна» | participants.resolve → slots → текст с обоснованием |
| WF-08 | `calendar-event` | «поставь репетицию…» / callback выбора слота | подтверждение → calendar/create → notify участникам |
| WF-09 | `fallback` | нет интента / LLM упал | честный ответ + меню доступных действий |
| WF-10 | `callback` | нажатие кнопки | маршрутизация callback_data на нужный воркфлоу |
| WF-11 | `reminder-dispatch` | cron (scheduler вызывает webhook) | генерация текстов уведомлений, отправка через `/notify` |
| WF-12 | `weekly-digest` | cron пн 10:00 | сводка для org |

Правило: **каждый воркфлоу начинается с проверки роли** (повторно, поверх бота) и заканчивается валидатором фактов (§4 `docs/architecture/05`). Общий subworkflow `validate-answer` подключается как последний шаг — не копипастой, а ссылкой, иначе расхождения неизбежны.

### Промпты
Хранятся файлами в репозитории (`prompts/{wf}/{version}.md`), версия пишется в `diagnostics`. Промпт не содержит данных — только правила и формат.

---

## 5. Конфигурация

```env
# tg-gateway
TELEGRAM_TOKEN=
ADMIN_CHAT_ID=
DATABASE_URL=postgresql://…
TOOL_API_URL=http://tool-api:8000
N8N_BASE_URL=http://n8n:5678
N8N_SHARED_SECRET=
TOOL_API_TOKEN=

# n8n
TOOL_API_URL=http://tool-api:8000
TOOL_API_TOKEN=
N8N_SHARED_SECRET=

# LLM: одно место для провайдера (docs/infrastructure/75 §7.4).
# Роутер WF-01 собирается с этими же значениями: scripts/build_router_workflow.py
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=            # в n8n ключ хранится в credential, эта строка — для tool-api
LLM_MODEL=              # пусто → openai/gpt-4o-mini
OPENROUTER_API_KEY=     # устаревшее имя, работает как алиас
DEFAULT_MODEL=          # историческое имя n8n-контура, в коде не читается
GOOGLE_SA_JSON=/run/secrets/google-sa.json   # только для n8n не нужен: Google доступен лишь через tool-api
BUDGET_SPREADSHEET_ID=
SCHEDULE_SPREADSHEET_ID=
CALENDAR_ID=
```

## 6. Локальный запуск

```yaml
# docker-compose.yml (эскиз)
services:
  postgres:   { image: postgres:16, environment: [POSTGRES_PASSWORD=…], volumes: [pg:/var/lib/postgresql/data] }
  tool-api:   { build: ./services/tool-api, env_file: .env, depends_on: [postgres] }
  tg-gateway: { build: ./services/tg-gateway, env_file: .env, depends_on: [tool-api, n8n] }
  scheduler:  { build: ./services/scheduler, env_file: .env, depends_on: [tool-api] }
  n8n:        { image: n8nio/n8n, ports: ["5678:5678"], volumes: [n8n:/home/node/.n8n], env_file: .env }
```

Локально Telegram требует публичный HTTPS → ngrok/cloudflared для webhook, либо long polling (единственный реализованный режим). n8n импортирует воркфлоу из `n8n/workflows/*.json` при старте (init-контейнер).

### Переезд на удалённый сервер
1. `N8N_BASE_URL` → `https://n8n.<домен>`.
2. n8n остаётся в той же сети с `tool-api` (или через публичный `tool-api` с HMAC и allowlist IP).
3. Google-доступ не меняется (только у `tool-api`).
4. Секреты — заново, не переиспользовать локальные.

---

## 7. Что тестировать

- Контракт: JSON-schema на запрос/ответ,CI-валидация примеров.
- Идемпотентность: один и тот же `(chat_id, message_id)` дважды → одна запись в Sheets.
- Права: `troupe` вызывает `/tools/budget/commit` → 403 + `audit_event(access.denied)`.
- Валидатор фактов: подсунуть ответ с числом, которого нет в tool-данных → должен быть отброшен.
- Обрыв n8n на середине: pending в БД, после восстановления — доигрывание, без дублей.
- Смена роли при активном диалоге: следующее сообщение уже по новым правам.
