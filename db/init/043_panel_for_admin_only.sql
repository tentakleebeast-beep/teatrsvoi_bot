-- 043: подтверждение крупных сумм — организатору; админ-панель — только администратору.
-- Решение заказчика 29.10.2026: организатор подтверждает суммы, но панель ему не нужна —
-- у него в чате кнопки «Заявки» и «Пригласить», а крупные суммы приходят уведомлением
-- с кнопками (адресат определяется правом `budget.approve`, см. `_notify_capability`).
begin;

-- 1. Право на вход в админ-панель отдельным кодом: иначе панель открывалась любому,
--    у кого есть чтение участников или регистрационные права.
insert into rbac_capability (code, title, grp) values
  ('admin.panel', 'Открывать админ-панель', 'people')
on conflict (code) do nothing;

insert into rbac_role_capability (role, capability) values ('admin', 'admin.panel')
on conflict do nothing;

-- 2. Суммы подтверждает организатор (администратор тоже сохраняет это право).
insert into rbac_role_capability (role, capability) values ('org', 'budget.approve')
on conflict do nothing;

commit;

select r.code, string_agg(rc.capability, ', ' order by rc.capability) as caps
  from rbac_capability r
  join rbac_role_capability rc on rc.capability = r.code
 where r.code in ('admin.panel', 'budget.approve')
 group by r.code;
