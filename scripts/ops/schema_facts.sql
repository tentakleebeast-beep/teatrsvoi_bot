-- Что показывает «ремонт схемы прода» после миграций. Только чтение.
-- Один взгляд на то, что миграция реально сделала: статья, функции, триггер, папки.

select 'статья Прочее (доход)' as факт, count(*)::text as значение
  from category where name = 'Прочее (доход)'
union all
select 'функция ensure_project_folder',
       count(*)::text from pg_proc where proname = 'ensure_project_folder'
union all
select 'триггер trg_project_folder',
       count(*)::text from pg_trigger where tgname = 'trg_project_folder'
union all
select 'общих папок',
       count(*)::text from folder where scope = 'shared' and deleted_at is null;
