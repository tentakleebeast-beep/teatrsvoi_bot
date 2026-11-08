-- 016: исправляем семантику импортированной занятости
-- В файле «СВОИ Расписание»: 1 = ЗАНЯТ, 0 = свободен (подтверждено заказчиком 26.10.2026).
-- Импорт от 25.10 читал наоборот, поэтому занятость в БД была перевёрнута.
--
-- ВАЖНО: миграция НЕ идемпотентна по своей природе (инверсия), поэтому защищена
-- меткой в migration_marker. Повторный запуск ничего не делает.

begin;

create table if not exists migration_marker (
  key        text primary key,
  applied_at timestamptz not null default now(),
  note       text
);

do $$
declare
  уже_применена boolean;
begin
  select exists(select 1 from migration_marker where key = '016_availability_invert')
    into уже_применена;

  if уже_применена then
    raise notice '016 уже применена — инверсия пропущена';
    return;
  end if;

  update availability_hour
     set is_free = not is_free,
         kind = case when kind = 'busy' then 'free' else 'busy' end
   where kind in ('busy', 'free');

  insert into migration_marker (key, note)
  values ('016_availability_invert', 'инверсия выполнена');
end $$;

commit;
