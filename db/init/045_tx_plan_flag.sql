-- 045: «план или факт» у операции (решение 30.10.2026).
-- Планы не входят в баланс: их видно в бюджете, но денег они не двигают.
begin;
alter table budget_tx add column if not exists is_plan boolean not null default false;
comment on column budget_tx.is_plan is
  'Плановая операция: показывается в бюджете, но не входит в баланс';
commit;
