-- 024: персональное время на дорогу и признак «уже в театре»
--
-- Уточнения заказчика 27.10.2026:
--   1) дорога нужна ВСЕГДА — и если человек свободен с 18:00, он ещё не в театре;
--   2) время в пути у всех разное: у Виктора час, у Даины 15 минут;
--   3) если человек был занят в театре (аренда, хоз.работы), дорога не нужна.

begin;

alter table participant
  add column if not exists travel_minutes integer not null default 60;

alter table participant drop constraint if exists participant_travel_minutes_check;
alter table participant add constraint participant_travel_minutes_check
  check (travel_minutes between 0 and 240);

-- отметка «в этот час человек уже в театре»: дорога не добавляется
alter table availability_hour
  add column if not exists at_theatre boolean not null default false;

comment on column availability_hour.at_theatre is
  'Занят в театре (аренда, хоз.работы) — на дорогу время не тратится';

commit;

select count(*) as людей, min(travel_minutes) as минимум, max(travel_minutes) as максимум
from participant;
