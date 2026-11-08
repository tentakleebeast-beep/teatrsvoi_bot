-- 032: гранулярность записи в человеческий лист и карта источников
--
-- Проблема, которую это решает: база хранит доход по показам, а лист ведётся
-- по источникам («Марина», «от Даины»). Сопоставлять по сумме нельзя — двойной счёт.
-- Поэтому гранулярность становится настройкой, а соответствие «источник → статья»
-- хранится в карте.

begin;

create table if not exists finance_granularity (
    section text primary key
            check (section in ('dues', 'expense', 'show_income', 'show_cost', 'sublease')),
    mode    text not null default 'per_operation'
            check (mode in ('per_operation', 'per_source', 'per_month')),
    note    text
);

insert into finance_granularity (section, mode, note) values
    ('dues',        'per_operation', 'взнос каждого человека отдельной строкой'),
    ('expense',     'per_operation', 'расход отдельной строкой: так видно, на что ушло'),
    ('show_income', 'per_source',    'доход по источникам: билеты, курсы, донаты'),
    ('show_cost',   'per_operation', 'вложения в показ отдельной строкой'),
    ('sublease',    'per_source',    'субаренда по арендатору, а не по каждой оплате')
on conflict (section) do nothing;

create table if not exists finance_source_map (
    id          bigserial primary key,
    section     text not null
                check (section in ('dues', 'expense', 'show_income', 'show_cost', 'sublease')),
    label       text not null,               -- как написано в листе: «Марина», «от Даины»
    category_id bigint references category(id) on delete set null,
    project_id  bigint references project(id) on delete set null,
    note        text,
    created_at  timestamptz not null default now()
);

create unique index if not exists finance_source_map_uniq
    on finance_source_map (section, lower(label));

commit;
