-- 046: файлы бота — объектное хранилище, папки, теги, аудио, доступ и черновики агента.
-- Требования: docs/development/86. Байты живут в Garage (S3 API), здесь — истина о файлах.
--
-- Идемпотентно: deploy/server-deploy.sh прогоняет все db/init/*.sql подряд при каждом выкате,
-- поэтому каждый шаг обязан переживать повтор.

-- ═══════════════════════════════════════════════ 1. file_object: из заглушки под Drive — в файл
-- Заглушка появилась в 001 и ни разу не использовалась: drive_file_id был обязательным, а
-- сервис-аккаунт не может создавать файлы на Drive (квота 0, docs/infrastructure/09).
alter table file_object alter column drive_file_id drop not null;
alter table file_object add column if not exists object_key     text;
alter table file_object add column if not exists bucket         text;
alter table file_object add column if not exists sha256         char(64);
alter table file_object add column if not exists kind           text;
alter table file_object add column if not exists last_access_at timestamptz;
alter table file_object add column if not exists deleted_at     timestamptz;
alter table file_object add column if not exists cold_at        timestamptz;
alter table file_object add column if not exists search_tsv     tsvector;
alter table file_object add column if not exists meta           jsonb not null default '{}'::jsonb;

alter table file_object drop constraint if exists file_object_uploaded_via_check;
alter table file_object add constraint file_object_uploaded_via_check
  check (uploaded_via in ('human_drive','human_telegram','bot','webapp','agent'));

alter table file_object drop constraint if exists file_object_kind_check;
alter table file_object add constraint file_object_kind_check
  check (kind is null or kind in ('text','image','audio'));

alter table file_object drop constraint if exists file_object_scope_check;
alter table file_object add constraint file_object_scope_check
  check (scope in ('personal','shared'));

-- один ключ в бакете = одна строка: два объекта не должны ссылаться на одни байты
create unique index if not exists ux_file_object_key on file_object (object_key)
  where object_key is not null;
create index if not exists ix_file_object_sha on file_object (sha256) where sha256 is not null;
create index if not exists ix_file_object_owner on file_object (participant_id, created_at desc)
  where deleted_at is null;
create index if not exists ix_file_object_scope on file_object (scope, created_at desc)
  where deleted_at is null;

-- ═══════════════════════════════════════════════ 2. Папки: дерево, личные и общие
create table if not exists folder (
  id             bigserial primary key,
  scope          text not null check (scope in ('personal','shared')),
  participant_id bigint references participant(id) on delete cascade,  -- null для общих
  parent_id      bigint references folder(id) on delete cascade,
  project_id     bigint references project(id) on delete set null,      -- «Спектакли/<Пурга>»
  name           text not null,
  path           text not null,
  depth          int  not null default 1,
  created_by     bigint references participant(id),
  created_at     timestamptz not null default now(),
  deleted_at     timestamptz
);

-- Личная папка обязана иметь владельца, общая — не иметь: иначе «чья-то» папка станет общей
alter table folder drop constraint if exists folder_scope_owner_check;
alter table folder add constraint folder_scope_owner_check check (
  (scope = 'shared'   and participant_id is null) or
  (scope = 'personal' and participant_id is not null)
);

alter table folder drop constraint if exists folder_depth_check;
alter table folder add constraint folder_depth_check check (depth between 1 and 5);

-- Уникальность пути: два партиальных индекса вместо unique(scope, participant_id, path).
-- В Postgres NULL-ы не равны друг другу, поэтому unique с participant_id = null разрешал бы
-- две общие папки с одинаковым путём — именно та ошибка, которую потом ищут неделю.
create unique index if not exists ux_folder_personal_path on folder (participant_id, path)
  where scope = 'personal' and deleted_at is null;
create unique index if not exists ux_folder_shared_path on folder (path)
  where scope = 'shared' and deleted_at is null;
create index if not exists ix_folder_parent on folder (parent_id) where deleted_at is null;
create index if not exists ix_folder_project on folder (project_id) where project_id is not null;

-- Глубина считается от родителя, а не присылается приложением: единственное место правды
create or replace function folder_set_depth() returns trigger
language plpgsql as $$
declare
  parent_depth int;
begin
  if new.parent_id is null then
    new.depth := 1;
  else
    select depth into parent_depth from folder where id = new.parent_id;
    if parent_depth is null then
      raise exception 'родительская папка % не найдена', new.parent_id;
    end if;
    new.depth := parent_depth + 1;
  end if;
  if new.depth > 5 then
    raise exception 'вложенность папок больше 5 уровней (FR-K12)';
  end if;
  return new;
end $$;

drop trigger if exists trg_folder_depth on folder;
create trigger trg_folder_depth before insert or update of parent_id on folder
  for each row execute function folder_set_depth();

-- файл в папке: position — порядок треков, null = сортировать по имени (FR-M4)
create table if not exists file_folder (
  file_id   bigint not null references file_object(id) on delete cascade,
  folder_id bigint not null references folder(id) on delete cascade,
  position  int,
  added_at  timestamptz not null default now(),
  primary key (file_id, folder_id)
);
create index if not exists ix_file_folder_folder on file_folder (folder_id, position nulls last);

-- ═══════════════════════════════════════════════ 3. Теги: общий словарь, черновики на утверждение
create table if not exists tag (
  id          bigserial primary key,
  slug        text not null unique,
  title       text not null unique,
  kind        text not null default 'topic' check (kind in ('topic','project','role')),
  status      text not null default 'approved' check (status in ('approved','draft','rejected')),
  created_by  bigint references participant(id),
  approved_by bigint references participant(id),
  approved_at timestamptz,
  created_at  timestamptz not null default now()
);

create table if not exists file_tag (
  file_id bigint not null references file_object(id) on delete cascade,
  tag_id  bigint not null references tag(id) on delete cascade,
  primary key (file_id, tag_id)
);
create index if not exists ix_file_tag_tag on file_tag (tag_id);

-- ═══════════════════════════════════════════════ 4. Текст и характеристики аудио
create table if not exists file_text (
  file_id      bigint primary key references file_object(id) on delete cascade,
  body         text not null default '',
  lang         text,
  status       text not null default 'ok' check (status in ('ok','text_unavailable')),
  extracted_at timestamptz not null default now()
);

-- Аудио без перекодирования: только то, что сказал ffprobe (FR-M1)
create table if not exists file_audio (
  file_id      bigint primary key references file_object(id) on delete cascade,
  duration_sec int check (duration_sec is null or duration_sec >= 0),
  bitrate_kbps int check (bitrate_kbps is null or bitrate_kbps > 0),
  format       text,
  title        text,
  artist       text,
  probed_at    timestamptz not null default now()
);

-- ═══════════════════════════════════════════════ 5. Доступ админа к личному файлу
-- Личное видит только владелец; админ — по причине, с уведомлением и записью в аудит (FR-K9)
create table if not exists access_grant (
  id          bigserial primary key,
  file_id     bigint not null references file_object(id) on delete cascade,
  admin_id    bigint not null references participant(id),
  reason      text not null check (length(btrim(reason)) > 0),
  notified_at timestamptz,
  expires_at  timestamptz,
  created_at  timestamptz not null default now()
);
-- один действующий доступ на пару «файл — админ»: второй запрос не создаёт дубль
create unique index if not exists ux_access_grant_pair on access_grant (file_id, admin_id);

-- ═══════════════════════════════════════════════ 6. Черновики действий агента
create table if not exists agent_action (
  id             bigserial primary key,
  participant_id bigint not null references participant(id) on delete cascade,
  file_id        bigint references file_object(id) on delete cascade,
  kind           text not null check (kind in ('move','rename','tag','summarize','answer')),
  payload        jsonb not null default '{}'::jsonb,
  status         text not null default 'proposed'
                   check (status in ('proposed','applied','rejected','expired')),
  proposed_at    timestamptz not null default now(),
  resolved_at    timestamptz
);
create index if not exists ix_agent_action_open on agent_action (participant_id, status)
  where status = 'proposed';

-- ═══════════════════════════════════════════════ 7. Поиск: имя + текст + теги
create or replace function refresh_file_tsv(p_file_id bigint) returns void
language sql as $$
  update file_object f
     set search_tsv = to_tsvector(
           'russian',
           coalesce(f.name, '') || ' ' ||
           coalesce((select t.body from file_text t where t.file_id = f.id), '') || ' ' ||
           coalesce((select string_agg(g.title, ' ')
                       from file_tag ft join tag g on g.id = ft.tag_id
                      where ft.file_id = f.id), ''))
   where f.id = p_file_id;
$$;

create or replace function file_object_tsv_trigger() returns trigger
language plpgsql as $$
begin
  perform refresh_file_tsv(new.id);
  return null;
end $$;

drop trigger if exists trg_file_object_tsv on file_object;
create trigger trg_file_object_tsv after insert or update of name on file_object
  for each row execute function file_object_tsv_trigger();

create or replace function file_text_tsv_trigger() returns trigger
language plpgsql as $$
begin
  perform refresh_file_tsv(coalesce(new.file_id, old.file_id));
  return null;
end $$;

drop trigger if exists trg_file_text_tsv on file_text;
create trigger trg_file_text_tsv after insert or update of body on file_text
  for each row execute function file_text_tsv_trigger();

create or replace function file_tag_tsv_trigger() returns trigger
language plpgsql as $$
begin
  perform refresh_file_tsv(coalesce(new.file_id, old.file_id));
  return null;
end $$;

drop trigger if exists trg_file_tag_tsv on file_tag;
create trigger trg_file_tag_tsv after insert or delete on file_tag
  for each row execute function file_tag_tsv_trigger();

create index if not exists ix_file_object_fts on file_object using gin (search_tsv);

-- ═══════════════════════════════════════════════ 8. Инварианты файлов
-- Отдельное представление, а не строки в v_invariant_checks: там деньги, здесь файлы.
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
)
select * from f1 union all select * from f2 union all select * from f3 union all select * from f4
union all select * from f5 union all select * from f6 union all select * from f7
union all select * from f8 union all select * from f9 union all select * from f10;
