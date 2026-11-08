-- 005: поля и справочники под бизнес-правила (docs/architecture/11-business-rules.md)
--   * доли от сборов: rate_pct + base (сборы показа)
--   * авансы: is_advance + service_period (период услуги, без переноса суммы)
--   * планируемые операции: status='expected' + budget_plan
--   * тарифы взносов: dues_plan
--   * опечатки и синонимы статей: article_alias
--   * инварианты в виде представления: v_invariant_checks

begin;

alter table budget_tx add column if not exists rate_pct      numeric(5,2);
alter table budget_tx add column if not exists base_tx_id    bigint references budget_tx(id);
alter table budget_tx add column if not exists service_period text;
alter table budget_tx add column if not exists is_advance     boolean not null default false;

alter table budget_tx drop constraint if exists budget_tx_status_check;
update budget_tx set status = 'confirmed'
 where status not in ('draft','expected','pending_approval','confirmed','rejected','reversed');
alter table budget_tx add constraint budget_tx_status_check
  check (status in ('draft','expected','pending_approval','confirmed','rejected','reversed'));

-- синонимы и опечатки статей: «комуналка», «Тренинги Крылов», «низ афиши»
create table if not exists article_alias (
  id          bigserial primary key,
  raw_text    text not null unique,
  canonical   text not null,
  category_id bigint references category(id),
  project_id  bigint references project(id),
  confirmed_by bigint references participant(id),
  created_at  timestamptz not null default now()
);

-- тарифы взносов: у людей разные суммы (10000 / 6000 / 50000 за несколько месяцев)
create table if not exists dues_plan (
  id             bigserial primary key,
  participant_id bigint not null references participant(id),
  period         text not null,
  tariff         numeric(12,2) not null,
  amount         numeric(12,2) not null,
  reason         text,                       -- 'льгота', 'неполный месяц', 'предоплата за 5 мес'
  created_at     timestamptz not null default now(),
  unique (participant_id, period)
);

-- план: ожидаемые доходы и расходы по периодам (не входят в факт)
create table if not exists budget_plan (
  id             bigserial primary key,
  period         text not null,
  block          text not null
                   check (block in ('dues','expense','show_income','show_cost','sublease')),
  category_id    bigint references category(id),
  amount_planned numeric(12,2) not null,
  note           text,
  status         text not null default 'planned' check (status in ('planned','cancelled')),
  created_at     timestamptz not null default now(),
  unique (period, block, category_id)
);

create index if not exists ix_tx_expected on budget_tx (status) where status = 'expected';
create index if not exists ix_tx_advance  on budget_tx (service_period) where is_advance;

-- ---------------------------------------------------------------- итоги и баланс
create or replace view v_period_totals as
select p.period,
       p.quality,
       coalesce((select sum(amount_due) from dues_charge d where d.period = p.period), 0)      as income_dues,
       coalesce(sum(t.amount) filter (where t.block = 'show_income'), 0)                       as income_shows,
       coalesce(sum(t.amount) filter (where t.block = 'sublease'), 0)                          as income_sublease,
       coalesce((select sum(amount_due) from dues_charge d where d.period = p.period), 0)
         + coalesce(sum(t.amount) filter (where t.block in ('show_income','sublease')), 0)     as income_total,
       coalesce(sum(t.amount) filter (where t.block = 'expense'), 0)                           as expense_studio,
       coalesce(sum(t.amount) filter (where t.block = 'show_cost'), 0)                         as expense_shows,
       coalesce(sum(t.amount) filter (where t.block in ('expense','show_cost')), 0)            as expense_total
from budget_period p
left join budget_tx t on t.period = p.period and t.status = 'confirmed'
group by p.period, p.quality;

create or replace view v_balance as
select
  (select coalesce(sum(amount_due),0) from dues_charge)                                as dues_total,
  (select coalesce(sum(amount),0) from budget_tx where tx_type='income'  and status='confirmed') as income_tx,
  (select coalesce(sum(amount),0) from budget_tx where tx_type='expense' and status='confirmed') as expense_tx,
  (select coalesce(sum(amount_due),0) from dues_charge)
   + (select coalesce(sum(amount),0) from budget_tx where tx_type='income' and status='confirmed')
   - (select coalesce(sum(amount),0) from budget_tx where tx_type='expense' and status='confirmed') as balance;

-- ---------------------------------------------------------------- инварианты
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
)
select * from i1 union all select * from i2 union all select * from i3
union all select * from i5 union all select * from i6
union all select * from i7 union all select * from i8;

commit;
