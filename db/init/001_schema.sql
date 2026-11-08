-- ============================================================================
-- Схема БД «Театр-Студия СВОИ» — v2 (после анализа реальных файлов)
-- Postgres 16. Применяется при первом старте контейнера (db/init) или вручную.
--
-- Принцип разделения:
--   * «пользовательские» таблицы — то, что человек ведёт руками в Google Sheets
--     (бюджет по месяцам, часовая матрица занятости). Истина для человека.
--   * «ботовские» таблицы — нормализованный индекс, состояние, аудит, привязки.
--     Истина для бота. Между ними — sheet_binding / row_map / import_*.
-- ============================================================================

-- ---------------------------------------------------------------- идентичность
create table if not exists participant (
  id              bigserial primary key,
  tg_chat_id      bigint not null unique,
  display_name    text not null,                 -- «Иванов Виктор» (как в финансах)
  person_key      text unique,                   -- «иванов» — ключ сопоставления
  role            text not null default 'troupe'
                    check (role in ('admin','org','troupe','student')),
  status          text not null default 'pending'
                    check (status in ('pending','active','blocked','archived')),
  drive_folder    text,
  is_active       boolean not null default true, -- учится/играет сейчас
  invited_by      bigint,
  invite_code_id  bigint,
  created_at      timestamptz not null default now(),
  activated_at    timestamptz
);

-- В файлах люди названы по-разному: «Иванов Виктор» (финансы),
-- «Витя М» (лист занятости), «Крылов Виктор» (статья расхода = гонорар).
create table if not exists person_alias (
  id             bigserial primary key,
  participant_id bigint not null references participant(id) on delete cascade,
  alias          text not null,                  -- нормализовано: lower, без ё
  alias_kind     text not null default 'name'
                   check (alias_kind in ('name','short','tg_username','payee','external')),
  source         text not null default 'manual' check (source in ('manual','import','bot')),
  created_at     timestamptz not null default now(),
  unique (alias)
);

create table if not exists invite_code (
  id          bigserial primary key,
  code        text not null unique,
  role        text not null check (role in ('admin','org','troupe','student')),
  created_by  bigint not null,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null,
  max_uses    int not null default 1,
  uses        int not null default 0,
  used_by     bigint
);

create table if not exists invite_attempt (
  id       bigserial primary key,
  chat_id  bigint not null,
  ts       timestamptz not null default now(),
  ok       boolean not null
);
create index if not exists ix_invite_attempt on invite_attempt (chat_id, ts);

-- ---------------------------------------------------------------- справочники
-- Проекты = спектакли и курсы. В файле это листы: «Жизнь насекомых», «Пурга», «Бунин».
create table if not exists project (
  id          bigserial primary key,
  name        text not null unique,
  kind        text not null default 'show'
                check (kind in ('show','course','training','other')),
  status      text not null default 'active'
                check (status in ('planned','active','archive')),
  starts_on   date,
  ends_on     date,
  created_at  timestamptz not null default now()
);

-- Категории расхода/дохода. В файле — свободный текст в колонке «Статья»:
-- «Аренда Прачки», «Коммуналка», «Съемки Бунин (долг)», «Ремонт уборная».
create table if not exists category (
  id          bigserial primary key,
  name        text not null unique,              -- каноническое имя
  direction   text not null default 'expense'
                check (direction in ('expense','income','both')),
  parent_id   bigint references category(id),
  created_at  timestamptz not null default now()
);

-- Сырые «статьи» из файлов -> категории/контрагенты/проекты.
-- Позволяет не ломать привычные формулировки человека.
create table if not exists article_map (
  id            bigserial primary key,
  raw_text      text not null unique,            -- «Съемки Бунин (долг)» как есть
  category_id   bigint references category(id),
  project_id    bigint references project(id),
  counterparty  text,
  kwargs        jsonb not null default '{}',     -- доп. признаки: {'is_debt':true}
  confirmed_by  bigint references participant(id),
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------- бюджет
-- Один месяц = один блок в листе «Финансы». Храним как «период» + плоский журнал.
create table if not exists budget_period (
  id             bigserial primary key,
  period         text not null unique,           -- 'YYYY-MM'
  status         text not null default 'open' check (status in ('open','closed')),
  opened_at      date,
  closed_at      date,
  note           text
);

-- Плоский журнал операций — единственная нормализованная форма.
-- Блоки листа (взносы / расходы / показы / вложения / субаренда) разворачиваются сюда.
create table if not exists budget_tx (
  id              bigserial primary key,
  period          text not null,                 -- 'YYYY-MM', дублирует budget_period.period для скорости
  block           text not null
                    check (block in ('dues','expense','show_income','show_invest','sublease')),
  tx_date         date not null,
  tx_type         text not null check (tx_type in ('income','expense','transfer')),
  amount          numeric(12,2) not null check (amount > 0),
  currency        text not null default 'RUB',
  category_id     bigint references category(id),
  project_id      bigint references project(id),
  counterparty    text,
  participant_id  bigint references participant(id),   -- если операция про конкретного человека
  article_raw     text,                                -- исходная «Статья» из файла
  purpose         text not null,
  method          text check (method in ('cash','card','transfer','unknown')),
  is_debt         boolean not null default false,
  status          text not null default 'confirmed'
                    check (status in ('draft','pending_approval','confirmed','rejected','reversed')),
  origin          text not null default 'import'
                    check (origin in ('bot','manual','import')),
  rev             int not null default 1,
  replaces        bigint references budget_tx(id),
  entered_by      bigint references participant(id),
  confirmed_by    bigint references participant(id),
  source_chat_id  bigint,
  source_message_id bigint,
  raw_text        text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (source_chat_id, source_message_id)
);
create index if not exists ix_tx_period on budget_tx (period, block);
create index if not exists ix_tx_date on budget_tx (tx_date desc);
create index if not exists ix_tx_cat on budget_tx (category_id, tx_date desc);

-- Взносы студийцев: начисление на человека за период (в файле колонка «Сумма» в блоке взносов)
create table if not exists dues_charge (
  id             bigserial primary key,
  participant_id bigint not null references participant(id),
  period         text not null,
  amount_due     numeric(12,2) not null,
  amount_paid    numeric(12,2) not null default 0,
  paid_on        date,
  payment_tx_id  bigint references budget_tx(id),
  status         text not null default 'due'
                   check (status in ('due','partial','paid','waived','skipped')),
  updated_at     timestamptz not null default now(),
  unique (participant_id, period)
);

-- Итоги месяца: вычисляются, но кэшируются для быстрых ответов и сверки с файлом
create table if not exists budget_month_total (
  period            text primary key,
  dues_total        numeric(12,2) not null default 0,
  expense_total     numeric(12,2) not null default 0,
  show_income_total numeric(12,2) not null default 0,
  invest_total      numeric(12,2) not null default 0,
  sublease_total    numeric(12,2) not null default 0,
  profit_total      numeric(12,2) not null default 0,
  computed_at       timestamptz not null default now()
);

-- ---------------------------------------------------------------- занятость
-- В файле — часовая матрица: строки = даты (53 дня), колонки = часы 08:00…,
-- значение 1 = свободен, 0 = занят. Храним почасово, это же и есть канон.
create table if not exists availability_hour (
  id             bigserial primary key,
  participant_id bigint not null references participant(id) on delete cascade,
  day            date not null,
  hour           int not null check (hour between 0 and 23),
  is_free        boolean not null,
  kind           text not null default 'declared'
                   check (kind in ('declared','preferred','imported')),
  source         text not null default 'bot'
                   check (source in ('bot','sheet_import','admin')),
  raw_text       text,
  updated_at     timestamptz not null default now(),
  unique (participant_id, day, hour)
);
create index if not exists ix_avail_day on availability_hour (day, hour);

-- События (репетиции, тренинги, курсы, показы)
create table if not exists event_plan (
  id             bigserial primary key,
  title          text not null,
  kind           text not null check (kind in ('rehearsal','training','course','show','meeting','other')),
  project_id     bigint references project(id),
  starts_at      timestamptz not null,
  ends_at        timestamptz not null,
  location       text,
  participant_ids bigint[] not null default '{}',
  status         text not null default 'proposed'
                   check (status in ('proposed','confirmed','cancelled','done')),
  gcal_event_id  text,
  created_by     bigint references participant(id),
  created_at     timestamptz not null default now(),
  check (ends_at > starts_at)
);

create table if not exists slot_proposal (
  id            bigserial primary key,
  request       jsonb not null,
  candidates    jsonb not null,
  chosen_index  int,
  event_plan_id bigint references event_plan(id),
  created_by    bigint references participant(id),
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------- заметки
-- ВАЖНО: сервис-аккаунт не может создавать файлы на Drive (квота 0).
-- Поэтому заметки — сначала в БД; выгрузка в Drive возможна только в файл,
-- созданный человеком (или при появлении Shared Drive / OAuth-аккаунта).
create table if not exists note (
  id             bigserial primary key,
  participant_id bigint not null references participant(id) on delete cascade,
  title          text not null,
  body           text not null,
  tags           text[] not null default '{}',
  scope          text not null default 'personal' check (scope in ('personal','shared')),
  project_id     bigint references project(id),
  drive_file_id  text,                          -- null = живёт только в БД
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  deleted_at     timestamptz
);

create table if not exists file_object (
  id             bigserial primary key,
  participant_id bigint references participant(id),
  drive_file_id  text not null unique,
  name           text not null,
  mime           text,
  size_bytes     bigint,
  scope          text not null default 'personal' check (scope in ('personal','shared')),
  uploaded_via   text not null default 'human_drive'
                   check (uploaded_via in ('human_drive','human_telegram','bot')),
  created_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------- привязка к листам
-- Описывает, где именно в Google Sheets живёт каждый «пользовательский» блок.
-- Бот никогда не вычисляет адреса сам — только по этой таблице.
create table if not exists sheet_binding (
  id             bigserial primary key,
  key            text not null unique,            -- 'budget.finance', 'schedule.people'
  spreadsheet_id text not null,
  sheet_title    text not null,
  layout         text not null
                   check (layout in ('monthly_blocks','hour_matrix','flat_table','key_value')),
  header_row     int,
  first_data_row int,
  first_col      int not null default 1,
  last_col       int,
  header_checksum text,
  meta           jsonb not null default '{}',     -- {projects:['Пурга'], hours:[8..23]}
  last_read_at   timestamptz,
  last_edit_time timestamptz,                     -- properties.lastEditTime (ADR-3)
  updated_at     timestamptz not null default now()
);

-- Сопоставление конкретной строки/ячейки листа с сущностью БД.
-- Замена «link by row number»: строки двигаются, id — нет.
create table if not exists row_map (
  id            bigserial primary key,
  binding_key   text not null references sheet_binding(key) on delete cascade,
  entity_type   text not null,
  entity_id     text not null,
  sheet_row     int,
  col_from      int,
  col_to        int,
  cell_key      text,                            -- для матриц: '2026-08-10T19'
  content_hash  text,
  last_seen_at  timestamptz not null default now(),
  is_deleted    boolean not null default false
);
-- выражение в UNIQUE-констрейнте Postgres не допускает, поэтому отдельным индексом
create unique index if not exists ux_row_map
  on row_map (binding_key, entity_type, entity_id, coalesce(cell_key, ''));
create index if not exists ix_row_map_row on row_map (binding_key, sheet_row);

-- ---------------------------------------------------------------- миграция
create table if not exists import_batch (
  id           bigserial primary key,
  kind         text not null
                 check (kind in ('budget','schedule','people','projects')),
  source       text not null,                    -- путь/ID файла
  source_hash  text,
  status       text not null default 'running'
                 check (status in ('running','needs_review','applied','failed')),
  stats        jsonb not null default '{}',      -- {rows:270, created:250, skipped:20}
  started_at   timestamptz not null default now(),
  finished_at  timestamptz
);

create table if not exists import_row (
  id               bigserial primary key,
  batch_id         bigint not null references import_batch(id) on delete cascade,
  sheet_title      text not null,
  sheet_row        int,
  sheet_col        int,
  raw              jsonb not null,
  resolved_type    text,                          -- 'dues_charge','expense','availability_hour'
  resolved_id      bigint,
  confidence       numeric(4,3),                  -- 0..1
  issue            text,                          -- 'unknown_person','ambiguous_project', NULL
  applied          boolean not null default false
);
create index if not exists ix_import_row_batch on import_row (batch_id, applied);

-- ---------------------------------------------------------------- уведомления/аудит/очередь
create table if not exists notification (
  id           bigserial primary key,
  recipient_id bigint references participant(id),
  kind         text not null,
  payload      jsonb not null default '{}',
  dedupe_key   text not null,
  scheduled_at timestamptz not null,
  status       text not null default 'scheduled'
                 check (status in ('scheduled','sent','failed','cancelled','skipped')),
  attempts     int not null default 0,
  last_error   text,
  sent_at      timestamptz,
  created_at   timestamptz not null default now(),
  unique (dedupe_key, recipient_id)
);
create index if not exists ix_notification_due on notification (status, scheduled_at);

create table if not exists notification_pref (
  participant_id bigint primary key references participant(id) on delete cascade,
  kinds_muted    text[] not null default '{}',
  quiet_from     time not null default '20:00',   -- Самара GMT+3, требование заказчика
  quiet_to       time not null default '08:00',
  digest_enabled boolean not null default true,
  updated_at     timestamptz not null default now()
);

create table if not exists audit_event (
  id             bigserial primary key,
  ts             timestamptz not null default now(),
  actor_type     text not null check (actor_type in ('human','bot','system')),
  actor_id       bigint,
  action         text not null,
  entity_type    text not null,
  entity_id      text,
  before         jsonb,
  after          jsonb,
  correlation_id uuid,
  source         jsonb not null default '{}',
  prev_hash      text,
  hash           text not null
);
create index if not exists ix_audit_ts on audit_event (ts desc);
create index if not exists ix_audit_entity on audit_event (entity_type, entity_id);

create table if not exists job (
  id           bigserial primary key,
  kind         text not null,
  run_at       timestamptz not null,
  payload      jsonb not null default '{}',
  status       text not null default 'pending'
                 check (status in ('pending','running','done','failed')),
  attempts     int not null default 0,
  locked_until timestamptz,
  last_error   text,
  created_at   timestamptz not null default now()
);
create index if not exists ix_job_due on job (status, run_at);

-- ---------------------------------------------------------------- представления
-- Сводная занятость: сколько человек свободно в каждый час (аналог листа «Свод СВОИ»)
create or replace view v_availability_summary as
select day, hour,
       count(*) filter (where is_free) as free_cnt,
       count(*) filter (where not is_free) as busy_cnt,
       count(*) as known_cnt
from availability_hour
group by day, hour;

-- Задолженность по взносам — вычисляется, а не хранится
create or replace view v_dues_balance as
select p.id as participant_id, p.display_name, d.period,
       d.amount_due, d.amount_paid,
       (d.amount_due - d.amount_paid) as balance, d.status
from dues_charge d
join participant p on p.id = d.participant_id
where d.amount_due <> d.amount_paid;
