-- 073: у файла два расположения — Garage и ссылка на Google Drive (E4-4, docs/development/192).
--
-- Что меняется
--   1. Колонки: `storage` ('garage' | 'drive'), `drive_url`, `drive_path`, `drive_mime`,
--      `description`. Три последние — метаданные ссылки и подпись человека; `null` допустим.
--   2. Backfill: строка-заглушка (`drive_file_id` без `object_key`) переходит в `drive`,
--      адрес собирается в том же `update` — иначе F12 краснеет на переносе без адреса.
--   3. Ограничения: `file_object_storage_check`, `file_object_kind_check` с `folder`.
--      Дизъюнкция `kind is null` в `kind_check` сохраняется: `null` в `check` не «проходит»,
--      а отвергается, а строки «файл» (`kind = null`) обязаны вставляться (§3.1).
--   4. Функция `file_ext(text)` — одно правило расширения для SQL и `app/file_kind.extension`.
--   5. Инварианты F11–F13 в `v_file_checks`; F1–F10 переносятся без изменений.
--   6. Индекс `ix_file_object_storage` под фильтр «только ссылки».
--
-- Номер. Изначально файл шёл под 072, но этот номер занят миграцией E1-4
-- (`072_google_event_calendar.sql`, влита в main коммитом e18c88c). Поэтому номер 073,
-- а `down_revision` — 072_google_event_calendar. Исправлено 23.11.2026 до мержа ветки:
-- название 072_files_two_locations перенумеровано в 073_files_two_locations.
--
-- Порядок шагов обязателен: колонки без `not null` (таблица может быть непустой) → backfill →
-- ограничения → функция → представление → индекс. Иначе инвариант краснеет на своей миграции.
--
-- Идемпотентно: deploy/server-deploy.sh прогоняет все db/init/*.sql подряд при каждом выкате,
-- поэтому повторный накат не меняет ни строк, ни ограничений.

-- ═══════════════════════════════════════════════ 1. Колонки
alter table file_object add column if not exists storage     text;
alter table file_object add column if not exists drive_url   text;
alter table file_object add column if not exists drive_path  text;
alter table file_object add column if not exists drive_mime  text;
alter table file_object add column if not exists description text;

-- ═══════════════════════════════════════════════ 2. Backfill — до ограничений
-- Заглушка 001 заводилась под файлы (папок в ней быть не могло), поэтому адрес — по форме файла.
do $$
declare
  moved int;
begin
  update file_object
     set storage = 'drive',
         drive_url = coalesce(drive_url,
                              'https://drive.google.com/file/d/' || drive_file_id || '/view')
   where drive_file_id is not null
     and object_key is null
     and storage is distinct from 'drive';
  get diagnostics moved = row_count;
  raise notice '073: строк, перенесённых в drive: %', moved;
end $$;

update file_object set storage = 'garage' where storage is null;

-- ═══════════════════════════════════════════════ 3. Ограничения
alter table file_object alter column storage set default 'garage';
alter table file_object alter column storage set not null;

alter table file_object drop constraint if exists file_object_storage_check;
alter table file_object add constraint file_object_storage_check
  check (storage in ('garage', 'drive'));

alter table file_object drop constraint if exists file_object_kind_check;
alter table file_object add constraint file_object_kind_check
  check (kind is null or kind in ('text', 'image', 'audio', 'folder'));

-- ═══════════════════════════════════════════════ 4. Расширение: одно правило на Python и SQL
-- Возвращает расширение с точкой и '' (никогда null), когда расширения нет: иначе `not in (...)`
-- дал бы NULL, и строка молча выпала бы из фильтра чипа.
create or replace function file_ext(p_name text) returns text
language sql immutable as $$
  select case
    when p_name is null or p_name = '' then ''
    when position('.' in p_name) = 0 then ''
    when p_name ~ '^\.[^.]*$' then lower(p_name)          -- «.docx»: ведущая точка — расширение
    else lower(substring(p_name from '\.[^.]*$'))
  end
$$;

-- ═══════════════════════════════════════════════ 5. Инварианты F1–F13
create or replace view v_file_checks as
with
f1 as (
  select 'F1' code, 'У каждого файла есть владелец' name,
         not exists (select 1 from file_object where participant_id is null) ok,
         coalesce((select count(*)::text from file_object where participant_id is null), '0') || ' без владельца' detail
),
f2 as (
  select 'F2', 'Файл знает, где его байты',
         not exists (select 1 from file_object
                      where deleted_at is null and object_key is null and drive_file_id is null),
         coalesce((select count(*)::text from file_object
                    where deleted_at is null and object_key is null and drive_file_id is null), '0') || ' без ключа'
),
f3 as (
  select 'F3', 'Размер файла положительный',
         not exists (select 1 from file_object where size_bytes is not null and size_bytes <= 0),
         coalesce((select count(*)::text from file_object where size_bytes is not null and size_bytes <= 0), '0') || ' строк'
),
f4 as (
  select 'F4', 'В ключе объекта нет имён и кириллицы',
         not exists (select 1 from file_object where object_key ~ '[А-Яа-яЁё]'),
         coalesce((select count(*)::text from file_object where object_key ~ '[А-Яа-яЁё]'), '0') || ' ключей с текстом'
),
f5 as (
  select 'F5', 'Личная папка принадлежит человеку',
         not exists (select 1 from folder where scope = 'personal' and participant_id is null),
         coalesce((select count(*)::text from folder where scope = 'personal' and participant_id is null), '0') || ' папок'
),
f6 as (
  select 'F6', 'Глубина папок не больше 5',
         not exists (select 1 from folder where depth > 5),
         coalesce((select max(depth)::text from folder), '0') || ' максимальная глубина'
),
f7 as (
  select 'F7', 'У папки нет потерянного родителя',
         not exists (select 1 from folder c where c.parent_id is not null
                       and not exists (select 1 from folder p where p.id = c.parent_id)),
         coalesce((select count(*)::text from folder c where c.parent_id is not null
                     and not exists (select 1 from folder p where p.id = c.parent_id)), '0') || ' папок'
),
f8 as (
  select 'F8', 'В папках нет удалённых файлов',
         not exists (select 1 from file_folder ff join file_object f on f.id = ff.file_id
                      where f.deleted_at is not null),
         coalesce((select count(*)::text from file_folder ff join file_object f on f.id = ff.file_id
                    where f.deleted_at is not null), '0') || ' связей'
),
f9 as (
  select 'F9', 'Путь папки соответствует её имени и родителю',
         not exists (
           select 1 from folder c
            where (c.parent_id is null and c.path <> c.name)
               or (c.parent_id is not null and not exists (
                     select 1 from folder p where p.id = c.parent_id
                       and c.path = p.path || '/' || c.name))),
         coalesce((select count(*)::text from folder c
                    where (c.parent_id is null and c.path <> c.name)
                       or (c.parent_id is not null and not exists (
                             select 1 from folder p where p.id = c.parent_id
                               and c.path = p.path || '/' || c.name))), '0') || ' папок'
),
f10 as (
  select 'F10', 'У доступа админа есть причина',
         not exists (select 1 from access_grant where length(btrim(reason)) = 0),
         coalesce((select count(*)::text from access_grant where length(btrim(reason)) = 0), '0') || ' записей'
),
-- F11: garage держит байты, а не ссылку. `deleted_at is null` — не поблажка: мягко удалённая
-- строка не видна ни в одном списке, и F2, из которого растёт инвариант, считает так же.
f11 as (
  select 'F11', 'Garage хранит байты, а не ссылку',
         not exists (select 1 from file_object
                      where deleted_at is null and storage = 'garage'
                        and (object_key is null or bucket is null or drive_url is not null)),
         coalesce((select count(*)::text from file_object
                    where deleted_at is null and storage = 'garage'
                      and (object_key is null or bucket is null or drive_url is not null)), '0') || ' строк'
),
-- F12: ссылка не держит байтов — ни ключа, ни размера байтов, ни отметок доступа.
f12 as (
  select 'F12', 'Ссылка на Drive не держит байтов',
         not exists (select 1 from file_object
                      where deleted_at is null and storage = 'drive'
                        and (drive_url is null or object_key is not null or bucket is not null
                             or sha256 is not null or cold_at is not null
                             or last_access_at is not null)),
         coalesce((select count(*)::text from file_object
                    where deleted_at is null and storage = 'drive'
                      and (drive_url is null or object_key is not null or bucket is not null
                           or sha256 is not null or cold_at is not null
                           or last_access_at is not null)), '0') || ' строк'
),
-- F13: папка Drive — только ссылка (V-123): `folder` не бывает garage-файлом.
f13 as (
  select 'F13', 'Папка Drive — только ссылка',
         not exists (select 1 from file_object where kind = 'folder' and storage <> 'drive'),
         coalesce((select count(*)::text from file_object
                    where kind = 'folder' and storage <> 'drive'), '0') || ' строк'
)
select * from f1 union all select * from f2 union all select * from f3 union all select * from f4
union all select * from f5 union all select * from f6 union all select * from f7
union all select * from f8 union all select * from f9 union all select * from f10
union all select * from f11 union all select * from f12 union all select * from f13;

-- ═══════════════════════════════════════════════ 6. Индекс под фильтр «только ссылки»
create index if not exists ix_file_object_storage on file_object (storage, created_at desc)
  where deleted_at is null;
