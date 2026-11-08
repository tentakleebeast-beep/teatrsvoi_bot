-- Аудит данных перед чисткой и импортом. Только чтение.
-- Запускается на сервере: psql -f audit_data.sql

\echo == участники, которых коснётся чистка: связанные строки ==
select p.id, p.display_name, p.status,
       (select count(*) from availability_hour a where a.participant_id = p.id) as avail_rows,
       (select count(*) from budget_tx b where b.participant_id = p.id) as budget_rows
from participant p
where p.id in (1, 18, 37, 38, 39, 2, 29)
order by p.id;

\echo == объём таблиц ==
select 'participant' as tbl, count(*) from participant
union all select 'availability_hour', count(*) from availability_hour
union all select 'budget_tx', count(*) from budget_tx
union all select 'dues_charge', count(*) from dues_charge
order by 1;

\echo == занятость: диапазон дат ==
select min(day) as first_day, max(day) as last_day, count(*) as rows from availability_hour;

\echo == занятость по месяцам ==
select to_char(day, 'YYYY-MM') as month, count(*) as rows, count(distinct participant_id) as people
from availability_hour group by 1 order by 1;

\echo == занятость: дубли ключа человек+день+час ==
select count(*) as dup_keys from (
  select participant_id, day, hour from availability_hour
  group by 1, 2, 3 having count(*) > 1) d;

\echo == бюджет: диапазон и блоки ==
select min(tx_date) as first_date, max(tx_date) as last_date, count(*) as rows from budget_tx;

select block, count(*) as rows, sum(amount) as total
from budget_tx group by 1 order by 1;

\echo == бюджет по месяцам ==
select period, count(*) as rows, sum(amount) as total
from budget_tx group by 1 order by 1;

\echo == бюджет: возможные дубли (период+блок+дата+сумма+статья) ==
select count(*) as dup_keys from (
  select period, block, tx_date, amount, coalesce(article_raw, '')
  from budget_tx group by 1, 2, 3, 4, 5 having count(*) > 1) d;
