-- 075: проекты и деньги события (E6-2, docs/development/196 §3.5, дизайн 191).
--
-- Что меняется
--   1. project: два новых типа (`event`, `rental`) и три колонки — описание,
--      контрагент, ведущий. Ни одна существующая строка тип не получает:
--      миграция только расширяет допустимые значения (§3.6).
--   2. event_plan.revenue_amount — сумма события аренды/показа арендатора.
--   3. budget_tx.event_id + частичный уникальный индекс: событие порождает ровно
--      одну операцию; origin допускает 'event' — признак производной строки.
--   4. budget_plan.project_id + новая уникальность (nulls not distinct): бюджет
--      студии остаётся одним, у проектов появляется своя ось плана.
--   5. Статья «Субаренда» переименовывается в «Аренда (доход)» (V-126).
--   6. v_invariant_checks пересобирается с I1–I8 **плюс** I11 и I12. Без пересборки
--      инварианты были бы объявлены и не проверялись — ровно дефект I9/I10 (R-1).
--
-- Номер. В дизайне 191 §8 стоял «071», но 071 занят, 072 — Google, 073 — файлы,
-- 074 — ветка wt/t_751ba368 (типы событий). Максимум по всем ссылкам — 074, берём 075.
-- Порядок с 074 не конфликтует: обе миграции аддитивные.
--
-- Идемпотентно: deploy/server-deploy.sh прогоняет все db/init/*.sql подряд, поэтому
-- повторный накат не меняет ни строк, ни ограничений.

-- ═══════════════════════════════════════════════ 1. project
alter table project drop constraint if exists project_kind_check;
alter table project add constraint project_kind_check
  check (kind in ('show', 'event', 'rental', 'course', 'training', 'other'));

alter table project add column if not exists description text;
alter table project add column if not exists counterparty text;
alter table project add column if not exists lead_participant_id bigint references participant(id);

-- ═══════════════════════════════════════════════ 2. event_plan.revenue_amount
alter table event_plan add column if not exists revenue_amount numeric(12,2);
alter table event_plan drop constraint if exists event_plan_revenue_amount_check;
alter table event_plan add constraint event_plan_revenue_amount_check
  check (revenue_amount is null or revenue_amount >= 0);

-- ═══════════════════════════════════════════════ 3. budget_tx.event_id
alter table budget_tx add column if not exists event_id bigint references event_plan(id);
create unique index if not exists ux_budget_tx_event on budget_tx (event_id)
  where event_id is not null;

alter table budget_tx drop constraint if exists budget_tx_origin_check;
alter table budget_tx add constraint budget_tx_origin_check
  check (origin in ('bot', 'manual', 'import', 'event'));

-- ═══════════════════════════════════════════════ 4. budget_plan.project_id
alter table budget_plan add column if not exists project_id bigint references project(id);
alter table budget_plan drop constraint if exists budget_plan_period_block_category_id_key;
create unique index if not exists ux_budget_plan_project
  on budget_plan (period, block, category_id, project_id) nulls not distinct;

-- ═══════════════════════════════════════════════ 5. Статья «Аренда (доход)»
-- Если «Аренда (доход)» уже заведена руками, миграция молчит: слияние двух
-- статей — решение человека, а не миграции (§9).
update category set name = 'Аренда (доход)'
 where name = 'Субаренда'
   and not exists (select 1 from category where name = 'Аренда (доход)');

-- ═══════════════════════════════════════════════ 6. Инварианты I1–I8, I11, I12
create or replace view v_invariant_checks as
with
i1 as (
  select 'I1' code, 'Баланс = накопленный итог последнего периода' name,
         abs((select balance from v_balance)
             - (select sum(income_total - expense_total) from v_period_totals)) < 0.01 ok,
         concat('баланс ', (select round(balance,2) from v_balance)) detail
),
i2 as (
  select 'I2', 'Нет дублей операций',
         not exists (
           select 1 from budget_tx where status = 'confirmed'
           group by period, block, tx_date, amount, article_raw having count(*) > 1),
         coalesce((select count(*)::text from (
             select 1 from budget_tx where status='confirmed'
             group by period, block, tx_date, amount, article_raw having count(*) > 1) d), '0') || ' групп дублей'
),
i3 as (
  select 'I3', 'Тип операции соответствует категории',
         not exists (select 1 from budget_tx t join category c on c.id = t.category_id
                     where c.direction in ('income','expense') and c.direction <> t.tx_type),
         coalesce((select count(*)::text from budget_tx t join category c on c.id=t.category_id
                   where c.direction in ('income','expense') and c.direction <> t.tx_type), '0') || ' строк'
),
i5 as (
  select 'I5', 'Доли от сборов сходятся с базой',
         not exists (
           select 1 from budget_tx t
           where t.rate_pct is not null and t.base_tx_id is not null
             and abs(t.amount - round((select amount from budget_tx b where b.id = t.base_tx_id) * t.rate_pct / 100, 2))
                 > 0.01 * (select amount from budget_tx b where b.id = t.base_tx_id)),
         coalesce((select count(*)::text from budget_tx where rate_pct is not null), '0') || ' выплат с rate'
),
i6 as (
  select 'I6', 'У аванса указан период услуги',
         not exists (select 1 from budget_tx where is_advance and service_period is null),
         coalesce((select count(*)::text from budget_tx where is_advance and service_period is null), '0') || ' авансов без периода'
),
i7 as (
  select 'I7', 'У операции есть блок, категория и дата',
         not exists (select 1 from budget_tx where category_id is null or tx_date is null or block is null),
         coalesce((select count(*)::text from budget_tx where category_id is null or tx_date is null), '0') || ' неполных'
),
i8 as (
  select 'I8', 'План не попал в факт',
         not exists (select 1 from budget_tx where status = 'expected' and tx_type not in ('income','expense')),
         coalesce((select count(*)::text from budget_tx where status='expected'), '0') || ' ожидаемых операций'
),
i11 as (
  select 'I11', 'Доход аренды сходится с событием',
         not exists (
           select 1 from event_plan e
            where coalesce(e.revenue_amount, 0) > 0 and e.status <> 'cancelled'
              and (select count(*) from budget_tx t
                    where t.event_id = e.id and t.origin = 'event'
                      and t.status = 'confirmed'
                      and t.amount = e.revenue_amount
                      and t.tx_date = e.starts_at::date) <> 1)
         and not exists (
           select 1 from event_plan e join budget_tx t on t.event_id = e.id
            where e.status = 'cancelled' and t.status = 'confirmed'),
         coalesce((select count(*)::text from event_plan e
                    where coalesce(e.revenue_amount, 0) > 0
                      and (select count(*) from budget_tx t
                            where t.event_id = e.id and t.origin = 'event'
                              and t.status = 'confirmed'
                              and t.amount = e.revenue_amount
                              and t.tx_date = e.starts_at::date) <> 1), '0')
           || ' событий без верной операции'
),
i12 as (
  select 'I12', 'Производная операция принадлежит арендатору',
         not exists (
           select 1 from budget_tx t
             left join project p on p.id = t.project_id
            where t.origin = 'event'
              and (t.event_id is null or p.id is null or p.kind <> 'rental')),
         coalesce((select count(*)::text from budget_tx t
                     left join project p on p.id = t.project_id
                    where t.origin = 'event'
                      and (t.event_id is null or p.id is null or p.kind <> 'rental')), '0')
           || ' операций не у арендатора'
)
select * from i1 union all select * from i2 union all select * from i3
union all select * from i5 union all select * from i6
union all select * from i7 union all select * from i8
union all select * from i11 union all select * from i12;

commit;
