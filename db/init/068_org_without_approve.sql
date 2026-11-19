-- 068: организатор больше не обрабатывает заявки — решение владельца 19.11.2026.
-- Причина: организатор — пользователь студии (бюджет, расписание, занятость, файлы);
-- заявки и коды приглашений — работа администратора. Спецификация — карта
-- t_d114c251 (E11), документы — docs/architecture/02-access-control.md §1.
-- Что снимается: право `registration.approve` у роли `org`. Выпуск кодов
-- (`registration.code.issue`) у `org` не было никогда — db/init/008.
-- Идемпотентно: delete по паре (role, capability); повторный накат не падает.
-- Откат: insert into rbac_role_capability (role, capability)
--        values ('org', 'registration.approve') on conflict do nothing;
begin;
delete from rbac_role_capability
 where role = 'org' and capability = 'registration.approve';
commit;
