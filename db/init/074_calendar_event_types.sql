-- 074: типы и цвета событий календаря — таблица вместо трёх словарей кода.
--
-- Зачем (docs/development/190 §6–§8, дизайн 187 §5, ответы владельца V-080/V-084/V-087).
-- Тип события был зашит в `core.KIND_TITLES_RU`, `core.EVENT_COLORS` и локальный
-- словарь `assign.describe`, цвета — ещё и в `config/calendar-palette.yaml`.
-- Организатор не мог ни назначить цвет типу, ни завести свой тип без правки кода.
-- Теперь словарь — таблица: у неё есть уникальность буквы, проверка цвета и внешний
-- ключ, которых не даёт JSON в `app_setting`.
--
-- Номер. Карта E5 писалась, когда в `main` максимумом был 072
-- (`072_google_event_calendar.sql`), а невлитая ветка E4-4 держала свой
-- `072_files_two_locations.sql`. Чтобы номера не совпали при слиянии, взяли 074,
-- оставив 073 свободным. E4-4 влилась первой и заняла 073
-- (`073_files_two_locations.sql`), так что 074 идёт сразу за ней — пропуска
-- в цепочке нет.
--
-- Что делает:
--   1. таблица `calendar_event_type` + частичный уникальный индекс по букве;
--   2. засев 7 типов по таблице §7.2 (5 активных, project без цвета, meeting/other без буквы);
--   3. `event_plan.kind` — внешний ключ вместо `event_plan_kind_check`;
--   4. название возможности `calendar.write` дополняется словами о типах и цветах.
--
-- Данные не переносятся: коды засева совпадают с теми, что писались в `event_plan.kind`
-- (`rental`, `show`, `rehearsal`, `training`, `meeting`, `other`), поэтому старые строки
-- проходят внешний ключ без правки. Строк с `course` быть не может: 022 исключила его.
-- На боевой базе число строк с `meeting`/`other` считается перед накатом и пишется в
-- отчёт о накате (190 §7.3); эта карта на прод не ходит.
--
-- Идемпотентно: create table/index if not exists, insert ... on conflict do nothing,
-- drop constraint if exists перед добавлением.
--
-- Откат: alter table event_plan drop constraint event_plan_kind_fkey;
--        alter table event_plan add constraint event_plan_kind_check
--          check (kind in ('rehearsal','show','training','rental','meeting','other'));
--        drop table calendar_event_type;
begin;

create table if not exists calendar_event_type (
  code        text primary key check (code ~ '^[a-z][a-z0-9_]{1,31}$'),
  letter      text check (letter ~ '^[А-ЯЁ]$'),
  title       text not null check (char_length(btrim(title)) between 1 and 40),
  color_id    smallint check (color_id between 1 and 11),
  sort_order  smallint not null default 100 check (sort_order between 0 and 9999),
  is_active   boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  updated_by  bigint references participant(id),
  -- активный тип обязан быть полным: без буквы его не узнать в заголовке,
  -- без цвета его нечем красить
  constraint calendar_event_type_active_complete
    check (not is_active or (letter is not null and color_id is not null))
);

-- Буква уникальна среди типов, у которых она есть: у meeting/other буквы нет,
-- поэтому обычный unique не подошёл (190 §7.1).
create unique index if not exists ux_calendar_event_type_letter
  on calendar_event_type (letter) where letter is not null;

insert into calendar_event_type (code, letter, title, color_id, sort_order, is_active)
values
  ('rental',    'А', 'Аренда',    10,  10, true),
  ('show',      'С', 'Спектакль', 11,  20, true),
  ('rehearsal', 'Р', 'Репетиция',  9,  30, true),
  ('training',  'Т', 'Тренинг',    5,  40, true),
  ('project',   'П', 'Проект',  null,  50, false),
  ('meeting',   null, 'Встреча',   8,  90, false),
  ('other',     null, 'Другое',    8,  91, false)
on conflict (code) do nothing;

alter table event_plan drop constraint if exists event_plan_kind_check;
alter table event_plan drop constraint if exists event_plan_kind_fkey;
alter table event_plan add constraint event_plan_kind_fkey
  foreign key (kind) references calendar_event_type(code);

update rbac_capability
   set title = 'Создавать и отменять события и настраивать типы и цвета'
 where code = 'calendar.write';

commit;
