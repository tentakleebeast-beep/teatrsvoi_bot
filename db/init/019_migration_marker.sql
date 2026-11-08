-- 019: журнал применённых миграций
-- Причина: 016 переворачивает занятость и не была идемпотентной — повторный
-- накат снова инвертировал бы данные. Теперь каждая опасная миграция ставит метку.

begin;

create table if not exists migration_marker (
  key        text primary key,
  applied_at timestamptz not null default now(),
  note       text
);

-- 016 применена 26.10.2026 до появления этого журнала — фиксируем факт
insert into migration_marker (key, note)
values ('016_availability_invert', 'применена 26.10.2026, защита добавлена позже')
on conflict (key) do nothing;

commit;

select key, applied_at from migration_marker;
