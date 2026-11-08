-- 006: база для долей от сборов + чистка тестового периода
-- Доля вида «30% Крылову Пурга» считается от СУММЫ сборов показа (может быть несколько
-- спектаклей), поэтому храним base_amount, а не ссылку на одну операцию.

begin;

-- тестовая строка из раннего эксперимента (июнь 2026) — в данных театра её нет
delete from budget_tx where period = '2026-06';
delete from budget_period where period = '2026-06';

alter table budget_tx add column if not exists base_amount numeric(12,2);

-- заполняем rate_pct и base_amount для процентных выплат по периодам 2026-09/10
with bases as (
  select t.period, p.name as project, sum(t.amount) as base
  from budget_tx t join project p on p.id = t.project_id
  where t.block = 'show_income' and t.status = 'confirmed'
  group by t.period, p.name
)
update budget_tx t
   set rate_pct = (regexp_match(t.article_raw, '(\d{1,3})\s*%'))[1]::numeric,
       base_amount = b.base
  from bases b
 where t.block = 'show_cost'
   and t.article_raw ~ '\d{1,3}\s*%'
   and b.period = t.period
   and (t.article_raw ilike '%' || b.project || '%' or b.project is null);

-- для «21% Крылову» проект в тексте не указан — база = сборы периода
update budget_tx t
   set base_amount = (select coalesce(sum(amount),0) from budget_tx
                      where period = t.period and block = 'show_income' and status='confirmed')
 where t.block = 'show_cost' and t.rate_pct is not null and t.base_amount is null;

-- I5 переписан на base_amount
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
         not exists (select 1 from budget_tx where status='confirmed'
                     group by period, block, tx_date, amount, article_raw having count(*) > 1),
         coalesce((select count(*)::text from (select 1 from budget_tx where status='confirmed'
             group by period, block, tx_date, amount, article_raw having count(*) > 1) d),'0') || ' групп'
),
i3 as (
  select 'I3', 'Тип операции соответствует категории',
         not exists (select 1 from budget_tx t join category c on c.id=t.category_id
                     where c.direction in ('income','expense') and c.direction <> t.tx_type),
         coalesce((select count(*)::text from budget_tx t join category c on c.id=t.category_id
                   where c.direction in ('income','expense') and c.direction <> t.tx_type),'0') || ' строк'
),
i5 as (
  select 'I5', 'Доли от сборов сходятся с базой',
         not exists (select 1 from budget_tx
                     where rate_pct is not null and base_amount is not null
                       and abs(amount - round(base_amount * rate_pct / 100, 2)) > 0.01 * base_amount),
         coalesce((select count(*)::text from budget_tx where rate_pct is not null),'0')
           || ' выплат, из них с расхождением: '
           || coalesce((select count(*)::text from budget_tx
                        where rate_pct is not null
                          and abs(amount - round(base_amount*rate_pct/100,2)) > 0.01*base_amount),'0')
),
i6 as (
  select 'I6', 'У аванса указан период услуги',
         not exists (select 1 from budget_tx where is_advance and service_period is null),
         coalesce((select count(*)::text from budget_tx where is_advance and service_period is null),'0') || ' без периода'
),
i7 as (
  select 'I7', 'У операции есть блок, категория и дата',
         not exists (select 1 from budget_tx where category_id is null or tx_date is null or block is null),
         coalesce((select count(*)::text from budget_tx where category_id is null or tx_date is null),'0') || ' неполных'
),
i8 as (
  select 'I8', 'План не попал в факт',
         not exists (select 1 from budget_tx where status='expected' and tx_type not in ('income','expense')),
         coalesce((select count(*)::text from budget_tx where status='expected'),'0') || ' ожидаемых'
)
select * from i1 union all select * from i2 union all select * from i3
union all select * from i5 union all select * from i6
union all select * from i7 union all select * from i8;

commit;

select period, left(article_raw, 28) article, amount, rate_pct, base_amount,
       round(base_amount * rate_pct / 100, 2) as expected
from budget_tx where rate_pct is not null order by period, amount desc;
