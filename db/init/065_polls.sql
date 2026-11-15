-- 065. Опросы в боте (E8): конструктор, адресаты, прохождение, ответы, результаты.
--
-- Спецификация — docs/development/162-bot-polls.md §4 и §14.1.
-- Восемь таблиц: poll, poll_question, poll_option, poll_audience, poll_person,
-- poll_run, poll_answer, poll_viewer. Отдельной таблицы `invitation` нет:
-- приглашение — это строка `poll_run` со `status='invited'` плюс запись
-- очереди `notification` (решение D6 §14.1).
--
-- Файл идемпотентен: повторный прогон не меняет ни схемы, ни данных (AC-1).
-- Применимые файлы не правятся: меняется поведение — новый файл с новым номером.

create table if not exists poll (
  id          bigserial primary key,
  title       text not null check (title <> ''),
  intro       text,                       -- одна строка перед первым вопросом
  status      text not null default 'draft'
                check (status in ('draft','running','closed')),
  anonymous   boolean not null default false,
  closes_at   timestamptz,                -- после — ответы не принимаются
  created_by  bigint not null references participant(id),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  started_at  timestamptz,
  closed_at   timestamptz
);

create table if not exists poll_question (
  id          bigserial primary key,
  poll_id     bigint not null references poll(id) on delete cascade,
  position    int not null check (position > 0),
  text        text not null check (text <> ''),
  kind        text not null check (kind in ('single','multi','yesno','number','text')),
  required    boolean not null default true,
  free_text   boolean not null default false,
  max_choices int check (max_choices is null or max_choices > 0),
  unique (poll_id, position),
  -- «список без списка» — частая поломка конструкторов: у yesno/number/text
  -- вариантов нет, значит и свободного текста рядом с ними быть не может (D2).
  check (kind in ('single','multi') or free_text = false)
);

create table if not exists poll_option (
  id          bigserial primary key,
  question_id bigint not null references poll_question(id) on delete cascade,
  position    int not null,
  code        text not null check (char_length(code) between 1 and 2),
  label       text not null check (label <> ''),
  unique (question_id, position),
  unique (question_id, code)
);

create table if not exists poll_audience (          -- правила набора
  id         bigserial primary key,
  poll_id    bigint not null references poll(id) on delete cascade,
  kind       text not null check (kind in ('all_active','role','project')),
  role       text check (role in ('admin','org','master','troupe')),
  project_id bigint references project(id),
  check ((kind = 'role'    and role is not null and project_id is null)
      or (kind = 'project' and project_id is not null and role is null)
      or (kind = 'all_active' and role is null and project_id is null))
);

create table if not exists poll_person (            -- галочки и исключения
  poll_id        bigint not null references poll(id) on delete cascade,
  participant_id bigint not null references participant(id) on delete cascade,
  mode           text not null default 'include' check (mode in ('include','exclude')),
  primary key (poll_id, participant_id)
);

create table if not exists poll_run (               -- прохождение одним человеком
  id             bigserial primary key,
  poll_id        bigint not null references poll(id) on delete cascade,
  participant_id bigint not null references participant(id) on delete cascade,
  status         text not null default 'invited'
                   check (status in ('invited','in_progress','done','declined')),
  position       int not null default 1,            -- указатель черновика
  message_id     bigint,                            -- сообщение с текущим вопросом
  started_at     timestamptz,
  finished_at    timestamptz,
  updated_at     timestamptz not null default now(),
  unique (poll_id, participant_id)
);
create index if not exists ix_poll_run_open on poll_run (participant_id, status);

create table if not exists poll_answer (
  run_id       bigint not null references poll_run(id) on delete cascade,
  question_id  bigint not null references poll_question(id) on delete cascade,
  option_codes text[] not null default '{}',        -- для single/multi/yesno
  text_value   text,                                -- для text и «свой вариант»
  number_value numeric,                             -- для number
  skipped      boolean not null default false,
  answered_at  timestamptz not null default now(),
  primary key (run_id, question_id)                 -- один ответ на вопрос, не два
);

create table if not exists poll_viewer (            -- кому показать результаты
  poll_id        bigint not null references poll(id) on delete cascade,
  participant_id bigint references participant(id) on delete cascade,
  role           text check (role in ('admin','org','master','troupe')),
  check ((participant_id is null) <> (role is null))
);
-- Первичный ключ по трём колонкам здесь невозможен: человек и роль
-- взаимоисключающие, а колонки ключа обязаны быть not null. Уникальность — индексом
-- с coalesce (ловушка, на которой такие таблицы обычно и ломаются).
create unique index if not exists ux_poll_viewer
  on poll_viewer (poll_id, coalesce(participant_id, 0), coalesce(role, ''));

-- Права: создавать опросы (сейчас только admin, V-088) и видеть чужие результаты.
insert into rbac_capability (code, title, grp, description) values
  ('poll.manage', 'Опросы', 'polls', 'Создать опрос, отправить, закрыть, видеть свои результаты'),
  ('poll.results.all', 'Результаты всех опросов', 'polls', 'Видеть результаты любого опроса')
on conflict (code) do nothing;

insert into rbac_role_capability (role, capability)
select 'admin', 'poll.manage' where not exists (
  select 1 from rbac_role_capability where role='admin' and capability='poll.manage');
insert into rbac_role_capability (role, capability)
select 'admin', 'poll.results.all' where not exists (
  select 1 from rbac_role_capability where role='admin' and capability='poll.results.all');
