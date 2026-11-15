-- 066: организатор ведёт опросы наравне с администратором (V-088, docs/development/162 §3).
--
-- Ответ V-088 — «(б) админ и организатор», но 065 выдал `poll.manage` только роли
-- `admin`, и организатор получал отказ на `/webapp/polls` (дефект D1 отчёта 163).
-- Здесь дописывается одна строка матрицы прав; остальное из 065 не меняется.
--
-- `poll.results.all` организатору намеренно не выдаётся: по 162 §3 D8 он видит свои
-- опросы, чужие открываются адресно (`poll_viewer`), и это разные права.
--
-- Идемпотентно: повторный прогон строку не дублирует (`where not exists`).

insert into rbac_role_capability (role, capability)
select 'org', 'poll.manage' where not exists (
  select 1 from rbac_role_capability where role = 'org' and capability = 'poll.manage');