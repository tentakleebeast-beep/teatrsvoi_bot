-- 054: корни общих папок и автоматические папки спектаклей (Ш-6, docs/development/86 §3.1).
-- Перенумеровано с 047: номер 047 был занят дважды (047_feedback_answers.sql), а порядок
-- применения — сортировка по имени файла. Содержимое не менялось (docs/reports/115).
--
-- Зачем триггер, а не код: проекты (спектакли) создаются в двух местах уже сейчас —
-- `scripts/import_finance.py` и `tool-api/app/main.py` — и появятся новые. Одно место,
-- где папка гарантированно появится при любом способе создания проекта, — сама база.
--
-- Идемпотентно: повторный выкат не плодит папки и не падает.

-- ─────────────────────────────────────────────── корни общих папок
-- Названия — рабочая версия (решение заказчика: уточним при подключении сценариев).
-- Это данные, а не код: админ может переименовать, триггер ищет «Спектакли» по пути.
create or replace function ensure_shared_folder(p_parent_id bigint, p_name text,
                                                 p_path text) returns bigint
language plpgsql as $$
declare
  v_id bigint;
begin
  select id into v_id from folder
   where scope = 'shared' and path = p_path and deleted_at is null;
  if v_id is not null then
    return v_id;
  end if;
  insert into folder (scope, parent_id, name, path)
  values ('shared', p_parent_id, p_name, p_path)
  on conflict do nothing
  returning id into v_id;
  if v_id is null then            -- параллельная вставка успела раньше
    select id into v_id from folder
     where scope = 'shared' and path = p_path and deleted_at is null;
  end if;
  return v_id;
end $$;

-- ─────────────────────────────────────────────── папка спектакля
-- Вынесено в функцию без NEW, чтобы её можно было вызвать и для уже существующих
-- проектов: у plpgsql-триггера напрямую не вызвать без строки-источника.
create or replace function ensure_project_folder(p_id bigint, p_name text,
                                                 p_kind text, p_status text)
returns void
language plpgsql as $$
declare
  v_root bigint;
  v_show bigint;
  v_name text;
  v_path text;
  part text;
begin
  -- архивные спектакли папку не получают: они не в работе (FR-K13)
  if p_kind <> 'show' or p_status = 'archive' then
    return;
  end if;

  v_root := ensure_shared_folder(null, 'Спектакли', 'Спектакли');
  -- «/» и «\» в названии сломали бы путь: путь — это ключ, а не украшение
  v_name := replace(replace(btrim(p_name), '/', '-'), '\', '-');
  v_path := 'Спектакли/' || v_name;

  select id into v_show from folder
   where scope = 'shared' and path = v_path and deleted_at is null;
  if v_show is null then
    insert into folder (scope, parent_id, project_id, name, path)
    values ('shared', v_root, p_id, v_name, v_path)
    on conflict do nothing
    returning id into v_show;
    if v_show is null then
      select id into v_show from folder
       where scope = 'shared' and path = v_path and deleted_at is null;
    end if;
  end if;

  if v_show is null then
    return;
  end if;

  foreach part in array array['тексты', 'партитуры', 'заметки', 'музыка', 'шумы'] loop
    perform ensure_shared_folder(v_show, part, v_path || '/' || part);
  end loop;
end $$;

create or replace function project_folder_autocreate() returns trigger
language plpgsql as $$
begin
  perform ensure_project_folder(new.id, new.name, new.kind, new.status);
  return null;
end $$;

drop trigger if exists trg_project_folder on project;
create trigger trg_project_folder after insert on project
  for each row execute function project_folder_autocreate();

-- ─────────────────────────────────────────────── уже существующие проекты
do $$
declare
  root text;
  p record;
begin
  foreach root in array array['Хозяйство', 'Соцсети', 'Документы', 'Спектакли'] loop
    perform ensure_shared_folder(null, root, root);
  end loop;

  for p in select * from project loop
    perform ensure_project_folder(p.id, p.name, p.kind, p.status);
  end loop;
end $$;
