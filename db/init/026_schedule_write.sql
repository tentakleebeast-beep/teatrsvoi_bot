-- 026: право назначать события (docs/architecture/50, docs/architecture/51)
--
-- Поиск окон — чтение (schedule.slots). Назначение события — запись:
-- создаётся событие в календаре, запись в расписании и уведомления участникам.
-- Такое право даём организаторам и админам, но не труппе.

begin;

insert into rbac_capability (code, grp, title, description) values
  ('schedule.write', 'schedule', 'Назначать события',
   'Создавать события: календарь, расписание, уведомления')
on conflict (code) do nothing;

insert into rbac_role_capability (role, capability) values
  ('admin', 'schedule.write'),
  ('org', 'schedule.write')
on conflict do nothing;

commit;

select role, capability from rbac_role_capability
where capability = 'schedule.write' order by role;
