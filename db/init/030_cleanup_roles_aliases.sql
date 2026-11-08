-- 030: добираем то, что не добила миграция 017 + чистим псевдонимы
--
-- Найдено смоуком (docs/54):
--   1) роль student удалена в коде, но три CHECK-ограничения её всё ещё разрешают;
--   2) псевдонимы различались только регистром: «саша» и «Саша» у одного человека.

begin;

-- 1. student больше нигде не разрешён
update participant set role = 'troupe' where role = 'student';
update participant_role set role = 'troupe' where role = 'student';

alter table participant drop constraint if exists participant_role_check;
alter table participant add constraint participant_role_check
  check (role in ('admin', 'org', 'troupe'));

-- коды, выданные на student, переводим в troupe: роль student упразднена
update invite_code set role = 'troupe' where role = 'student';

alter table invite_code drop constraint if exists invite_code_role_check;
alter table invite_code add constraint invite_code_role_check
  check (role in ('admin', 'org', 'troupe'));

alter table participant_role drop constraint if exists participant_role_role_check;

-- 2. псевдонимы: убираем дубли по регистру, оставляя самую раннюю запись
delete from person_alias a
using person_alias b
where a.participant_id = b.participant_id
  and lower(a.alias) = lower(b.alias)
  and a.id > b.id;

create unique index if not exists person_alias_participant_alias_ci
  on person_alias (participant_id, lower(alias));

commit;

select count(*) as псевдонимов from person_alias;
select conname, pg_get_constraintdef(oid)
from pg_constraint
where conname in ('participant_role_check', 'invite_code_role_check')
order by conname;
