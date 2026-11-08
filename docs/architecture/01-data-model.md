# 01. Модель данных

> **n8n выведен из контура (V-026, 01.11.2026).** Где ниже n8n назван частью контура или
> источником разбора свободного текста, читать: `POST /tools/route` в tool-api
> (`services/tool-api/app/router_llm.py`); воркфлоу, публичный вебхук и переменные `N8N_*`
> удалены из деплоя. История контрактов — `docs/archive/29-n8n-scenarios.md`.

> **v2 актуальна.** После анализа реальных файлов схема переработана: см. `docs/architecture/10-data-model-v2.md`
> и рабочий DDL `db/init/001_schema.sql`. Ключевые отличия: почасовая занятость вместо интервалов,
> `block`/`period` в бюджете, справочник статей, `person_alias`, `sheet_binding`/`row_map`, `import_*`.
> Текст ниже — исходная версия, оставлен как история решений.

Postgres 16. Всё состояние бота — здесь. Google Sheets/Drive — внешние источники истины для денег и занятости, в БД хранится зеркало + метаданные синхронизации.

Принципы:
- Идентичность, права, аудит, уведомления — **только** в БД (Google об этом ничего не знает).
- Деньги и занятость — **истина в файле**, БД хранит индекс и историю для поиска/аудита.
- Append-only там, где важна история: `audit_event`, `budget_tx`, `notification`.
- Все временные метки — `timestamptz`, бизнес-даты — `date` в таймзоне `Europe/Moscow`.

---

## 1. Идентичность и доступ

```sql
create table participant (
  id              bigserial primary key,
  tg_chat_id      bigint not null unique,          -- личный чат = сессия
  tg_user_id      bigint,
  tg_username     text,
  display_name    text not null,
  person_key      text unique,                      -- человекочитаемый ключ: "антон-зайцев"
  role            text not null check (role in ('admin','org','troupe','student')),
  status          text not null default 'pending'
                    check (status in ('pending','active','blocked','archived')),
  drive_folder_id text,                             -- /TG_<chat_id>
  invited_by      bigint references participant(id),
  invite_code_id  bigint references invite_code(id),
  created_at      timestamptz not null default now(),
  activated_at    timestamptz,
  role_changed_at timestamptz,
  notes           text
);

create table invite_code (
  id          bigserial primary key,
  code        text not null unique,                 -- 8 символов, без похожих 0/O/1/I
  role        text not null check (role in ('org','troupe','student')),
  created_by  bigint not null references participant(id),
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null,
  used_by     bigint references participant(id),
  used_at     timestamptz,
  max_uses    int not null default 1,
  uses        int not null default 0
);

-- Алиасы: как человек назван в файлах бюджета/занятости
create table person_alias (
  id             bigserial primary key,
  participant_id bigint not null references participant(id) on delete cascade,
  alias          text not null,                     -- нормализованный: lower, без ё
  alias_kind     text not null default 'name'
                   check (alias_kind in ('name','tg_username','role_name','external')),
  created_at     timestamptz not null default now(),
  unique (alias)
);

create table session (
  id             bigserial primary key,
  participant_id bigint not null references participant(id) on delete cascade,
  chat_id        bigint not null,
  state          text,                              -- конечный автомат диалога
  state_payload  jsonb not null default '{}',
  last_message_at timestamptz,
  created_at     timestamptz not null default now(),
  unique (chat_id)
);
```

Инварианты:
- `admin` — ровно один, задаётся `ADMIN_CHAT_ID` в окружении при первом старте.
- `status='pending'` не имеет прав ни на что, кроме регистрации.
- `person_key` — человекочитаемый идентификатор вида «иванов-виктор» (`app/person_key.py`, миграция `041`): строчные буквы, `ё → е`, слова через дефис, без пробелов и скобок. Он же имя файла личных настроек `bot/manifests/participants/<key>.md`. Ключ не меняется при переименовании: это идентификатор, а не подпись.
- Поиск человека по имени — один на все интерфейсы (`app/person_match.py`, `GET /tools/participants/find`): псевдоним, полное имя, только имя, только фамилия, кусок слова или ключ. Ответы только три: найден, не найден, нужно выбрать. Если под запрос подходят двое, интерфейс показывает кандидатов, а не берёт первого.
- `blocked` — данные и история сохраняются, доступ закрыт.

---

## 2. События и сообщения

```sql
create table message_event (
  id             bigserial primary key,
  chat_id        bigint not null,
  message_id     bigint not null,
  participant_id bigint references participant(id),
  direction      text not null check (direction in ('in','out')),
  text           text,
  attachments    jsonb not null default '[]',       -- [{type,file_id,name,mime,size}]
  intent         text,                              -- заполняет n8n
  processed_at   timestamptz,
  error          text,
  created_at     timestamptz not null default now(),
  unique (chat_id, message_id, direction)           -- идемпотентность ретраев
);
create index on message_event (participant_id, created_at desc);
```

Хранение 90 дней (текст), затем чистится джобой; `audit_event` хранится дольше и на `message_event` не ссылается.

```sql
create table dead_letter (
  id             bigserial primary key,
  kind           text not null,                     -- 'n8n_unavailable','llm_error','sheet_conflict'
  participant_id bigint references participant(id),
  payload        jsonb not null,
  attempts       int not null default 0,
  last_error     text,
  status         text not null default 'open' check (status in ('open','replayed','dropped')),
  created_at     timestamptz not null default now(),
  resolved_at    timestamptz
);
```

---

## 3. Заметки и файлы

```sql
create table note (
  id             bigserial primary key,
  participant_id bigint not null references participant(id) on delete cascade,
  title          text not null,
  body           text not null,
  tags           text[] not null default '{}',
  scope          text not null default 'personal' check (scope in ('personal','shared')),
  drive_file_id  text,                              -- заметка = .md в папке пользователя
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  deleted_at     timestamptz
);
create index on note using gin (to_tsvector('russian', coalesce(title,'') || ' ' || coalesce(body,'')));

create table file_object (
  id             bigserial primary key,
  participant_id bigint references participant(id),  -- null = общий файл
  drive_file_id  text not null unique,
  name           text not null,
  mime           text,
  size_bytes     bigint,
  scope          text not null default 'personal' check (scope in ('personal','shared')),
  checksum       text,
  uploaded_via   text not null default 'telegram' check (uploaded_via in ('telegram','drive_ui','bot_generated')),
  created_at     timestamptz not null default now(),
  deleted_at     timestamptz
);
```

Правило доступа: `drive_file_id` для personal всегда вычисляется через `participant_id`; входящий идентификатор от пользователя/LLM не принимается.

---

## 4. Бюджет (зеркало + журнал)

Google Sheets — истина. Здесь индекс для быстрых запросов, аудита и реконсиляции.

```sql
create table budget_tx (
  id              bigserial primary key,             -- = tx_id в файле (колонка A)
  rev             int not null default 1,
  replaces        bigint references budget_tx(id),    -- отмена/исправление
  tx_date         date not null,
  tx_type         text not null check (tx_type in ('income','expense','transfer')),
  amount          numeric(12,2) not null check (amount > 0),
  currency        text not null default 'RUB',
  category        text not null,                     -- из budget-rules.yaml
  project         text,                              -- спектакль / курс / общий
  counterparty    text,
  purpose         text not null,
  method          text check (method in ('cash','card','transfer','unknown')),
  status          text not null default 'confirmed'
                    check (status in ('draft','pending_approval','confirmed','rejected','reversed')),
  origin          text not null check (origin in ('bot','manual')),
  entered_by      bigint references participant(id),
  source_chat_id  bigint,
  source_message_id bigint,
  sheet_row       int,                               -- кэш, не ключ
  confirmed_by    bigint references participant(id),
  confirmed_at    timestamptz,
  raw_text        text,                              -- исходное сообщение
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (source_chat_id, source_message_id)         -- защита от двойной записи при ретрае
);
create index on budget_tx (tx_date desc, tx_type);
create index on budget_tx (category, tx_date desc);

create table budget_sync_state (
  id              int primary key default 1,
  spreadsheet_id  text not null,
  sheet_name      text not null default 'Транзакции',
  last_row_count  int not null default 0,
  header_checksum text,
  last_read_at    timestamptz,
  last_write_at   timestamptz,
  constraint single_row check (id = 1)
);

create table budget_recon (
  id           bigserial primary key,
  run_at       timestamptz not null default now(),
  kind         text not null,      -- 'row_only_in_sheet','row_only_in_db','value_mismatch'
  tx_id        bigint,
  detail       jsonb not null,
  resolved     boolean not null default false
);
```

Инварианты:
- `tx_id` генерируется ботом (`timestamp_ms * 1000 + counter`, монотонно) и пишется в файл колонкой A, чтобы ручные и ботовские строки различались.
- `origin='manual'` — строки без `tx_id`, найденные при чтении; бот их **не переписывает**, только индексирует и показывает.
- Статус `pending_approval` остался в схеме от снятого механизма второго подтверждения (убрано 30.10.2026, `docs/architecture/25` §4): сейчас операция сразу пишется со `status='confirmed'`. Значение держится ради старых строк и таблицы `approval_request` (история схемы).
- Изменение = новая строка с `rev+1` и `replaces`, старая остаётся (история).

```sql
create table due_member (            -- взносы студийцев
  id             bigserial primary key,
  participant_id bigint not null references participant(id),
  period         text not null,                    -- '2026-10'
  amount_due     numeric(12,2) not null,
  amount_paid    numeric(12,2) not null default 0,
  paid_at        date,
  tx_id          bigint references budget_tx(id),
  status         text not null default 'due' check (status in ('due','partial','paid','waived')),
  updated_at     timestamptz not null default now(),
  unique (participant_id, period)
);
```

---

## 5. Занятость и расписание

```sql
create table availability (
  id             bigserial primary key,
  participant_id bigint not null references participant(id) on delete cascade,
  avail_date     date not null,
  start_time     time not null,
  end_time       time not null,
  kind           text not null check (kind in ('free','busy','preferred')),
  source         text not null default 'telegram' check (source in ('telegram','sheet_import','admin')),
  raw_text       text,
  valid_until    date,                              -- устаревание: по умолчанию +28 дней
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  check (end_time > start_time)
);
create index on availability (avail_date, participant_id);

create table event_plan (          -- репетиции, тренинги, показы
  id             bigserial primary key,
  title          text not null,
  kind           text not null check (kind in ('rehearsal','training','show','meeting','other')),
  starts_at      timestamptz not null,
  ends_at        timestamptz not null,
  location       text,
  project        text,
  participant_ids bigint[] not null default '{}',
  status         text not null default 'proposed'
                   check (status in ('proposed','confirmed','cancelled','done')),
  gcal_event_id  text,
  created_by     bigint references participant(id),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  check (ends_at > starts_at)
);

create table slot_proposal (
  id              bigserial primary key,
  request         jsonb not null,          -- {duration_min, participant_ids, window, constraints}
  candidates      jsonb not null,          -- [{start,end,score,reasons,unavailable[]}]
  chosen_index    int,
  event_plan_id   bigint references event_plan(id),
  created_by      bigint references participant(id),
  created_at      timestamptz not null default now()
);
```

---

## 6. Уведомления

```sql
create table notification (
  id              bigserial primary key,
  recipient_id    bigint references participant(id),
  kind            text not null,            -- см. docs/architecture/05 §1
  payload         jsonb not null default '{}',
  dedupe_key      text not null,            -- 'payment_due:12:2026-10'
  scheduled_at    timestamptz not null,
  quiet_hours_shifted boolean not null default false,
  status          text not null default 'scheduled'
                    check (status in ('scheduled','sent','failed','cancelled','skipped')),
  attempts        int not null default 0,
  last_error      text,
  sent_at         timestamptz,
  created_at      timestamptz not null default now(),
  unique (dedupe_key, recipient_id)
);
create index on notification (status, scheduled_at);

create table notification_pref (
  participant_id  bigint primary key references participant(id) on delete cascade,
  kinds_muted     text[] not null default '{}',
  quiet_from      time not null default '22:00',
  quiet_to        time not null default '09:00',
  digest_enabled  boolean not null default true,
  updated_at      timestamptz not null default now()
);
```

`dedupe_key` гарантирует, что «напоминание о взносе за октябрь» не придёт пять раз при перезапуске worker'а.

---

## 7. Аудит и технический лог

```sql
create table audit_event (
  id             bigserial primary key,
  ts             timestamptz not null default now(),
  actor_type     text not null check (actor_type in ('human','bot','system')),
  actor_id       bigint,                            -- participant.id, null для system
  action         text not null,                     -- 'budget.tx.create', 'participant.role.change'
  entity_type    text not null,
  entity_id      text,
  before         jsonb,
  after          jsonb,
  correlation_id uuid not null,                     -- один id на весь диалог/задачу
  source         jsonb not null default '{}',       -- {chat_id,message_id,tool_call_id}
  prev_hash      text,
  hash           text not null                      -- sha256(prev_hash || содержимое)
);
create index on audit_event (entity_type, entity_id);
create index on audit_event (ts desc);

create table tool_call_log (
  id             bigserial primary key,
  ts             timestamptz not null default now(),
  participant_id bigint references participant(id),
  endpoint       text not null,
  request        jsonb not null,
  response_hash  text,
  status_code    int,
  duration_ms    int,
  correlation_id uuid
);
```

`audit_event` — только INSERT. `UPDATE/DELETE` запрещены грантами роли БД. `hash`-цепочка позволяет проверить, что журнал не правился в обход.

---

## 8. Очередь задач (без Redis)

```sql
create table job (
  id             bigserial primary key,
  kind           text not null,      -- 'reminder.dispatch','budget.reconcile','availability.expire'
  run_at         timestamptz not null,
  payload        jsonb not null default '{}',
  status         text not null default 'pending'
                   check (status in ('pending','running','done','failed')),
  attempts       int not null default 0,
  locked_until   timestamptz,
  last_error     text,
  created_at     timestamptz not null default now()
);
create index on job (status, run_at);
```

Воркер берёт задачи через `SELECT … WHERE status='pending' AND run_at <= now() FOR UPDATE SKIP LOCKED LIMIT 10` — корректно при нескольких инстансах без внешнего брокера.

---

## 9. Что нужно получить от заказчика

Образцы `Бюджет.xlsx` и файла занятости → на их основе: точные имена листов/колонок, маппинг в `person_alias`, список категорий в `budget-rules.yaml`.
