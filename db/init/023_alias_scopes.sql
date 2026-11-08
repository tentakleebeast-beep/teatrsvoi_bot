-- 023: псевдонимы и роли с правильной областью действия
--
-- Было: unique (alias) глобально. Это запрещало три нормальных случая:
--   1) две «Саши» в труппе (короткие имена совпадают);
--   2) одна роль в разных спектаклях («Сэм» в двух постановках);
--   3) несколько названий одной роли (Няня / Нянюшка / Бабушка).
--
-- Стало: уникальность в пределах (псевдоним + вид + проект), плюс запрет дубля
-- одного и того же псевдонима у одного человека.

begin;

alter table person_alias drop constraint if exists person_alias_alias_key;

alter table person_alias
  add constraint person_alias_alias_scope_key
  unique nulls not distinct (alias, alias_kind, project_id);

alter table person_alias
  add constraint person_alias_person_alias_key
  unique (participant_id, alias);

-- быстрый поиск по роли внутри спектакля
create index if not exists ix_person_alias_role on person_alias (project_id, alias)
  where alias_kind = 'role';

commit;

select conname, pg_get_constraintdef(oid)
from pg_constraint where conrelid = 'person_alias'::regclass and contype in ('u','p');
