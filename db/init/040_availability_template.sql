-- 040: «обычная неделя» — шаблон занятости по дням недели (FR-C10).
-- Люди каждый месяц заполняют одно и то же: работа, семья, тренировки. Шаблон
-- избавляет от повторного ввода, а точечные правки остаются за человеком.
begin;

create table if not exists availability_template (
  participant_id bigint not null references participant(id) on delete cascade,
  weekday        int not null check (weekday between 0 and 6),   -- 0 = понедельник
  status         text not null check (status in ('free', 'busy', 'uncertain')),
  from_min       int check (from_min between 0 and 1440),
  to_min         int check (to_min between 0 and 1440),
  note           text,
  updated_at     timestamptz not null default now(),
  primary key (participant_id, weekday),
  constraint availability_template_span check (
    from_min is null or to_min is null or from_min < to_min
  )
);

comment on table availability_template is
  'Обычная неделя участника: состояние дня по дню недели (FR-C10)';

commit;

select count(*) as строк from availability_template;
