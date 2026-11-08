-- 044: права под новое меню организатора (решение 30.10.2026).
--   * подтверждение крупных сумм убрано совсем — право `budget.approve` больше не нужно;
--   * организатор сверяет листы бюджета сам — ему нужно `budget.import`;
--   * «Бюджет» в меню остаётся только администратору (пункт виден по `admin.panel`).
begin;

delete from rbac_role_capability where capability = 'budget.approve';
delete from rbac_capability where code = 'budget.approve';

insert into rbac_role_capability (role, capability) values ('org', 'budget.import')
on conflict do nothing;

commit;

select role, string_agg(capability, ', ' order by capability) as caps
  from rbac_role_capability
 where role in ('org', 'admin')
 group by role;
