-- 028: одинаковые короткие имена у разных людей (решение 27.10.2026)
--
-- В студии три Виктора, и все трое — «Витя». Это нормально: неоднозначность
-- разрешается вопросом «кого вы имели в виду», а не запретом на псевдоним.
--
-- Было: уникальность (alias, alias_kind, project_id) — не давала завести
-- второго «Витю». Стало: уникальность только для ролей внутри спектакля,
-- для имён достаточно «один и тот же псевдоним не повторяется у одного человека».

begin;

alter table person_alias
  drop constraint if exists person_alias_alias_scope_key;

create unique index if not exists person_alias_role_scope
  on person_alias (lower(alias), project_id)
  where alias_kind = 'role' and project_id is not null;

commit;

insert into person_alias (participant_id, alias, alias_kind, source)
select id, 'Витя', 'short', 'import' from participant where person_key = 'крылов'
on conflict (participant_id, alias) do nothing;

select a.alias, a.alias_kind, p.display_name
from person_alias a join participant p on p.id = a.participant_id
where lower(a.alias) = 'витя' order by p.display_name;
