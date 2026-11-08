-- 027: педагоги и режиссёры (решение заказчика 27.10.2026)
--
-- Виктор Крылов — режиссёр и педагог по актёрскому мастерству.
-- Светлана Попова — педагог по музыке.
--
-- Правила участия:
--   тренинг  — педагог входит в состав участников ВСЕГДА;
--   репетиция — режиссёр может входить, но это необязательно.
--
-- Работа этих людей с ботом (свои инструкции и интерфейсы) — отдельная задача.

begin;

alter table participant
  add column if not exists is_teacher boolean not null default false,
  add column if not exists is_director boolean not null default false,
  add column if not exists specialty text;

comment on column participant.is_teacher is 'Педагог: обязателен в составе тренинга';
comment on column participant.is_director is 'Режиссёр: может входить в состав репетиции';

update participant set is_director = true, is_teacher = true,
       specialty = coalesce(specialty, 'актёрское мастерство')
 where person_key like 'крылов%';

update participant set is_teacher = true,
       specialty = coalesce(specialty, 'музыка')
 where person_key like 'попова%';

commit;

select display_name, is_teacher, is_director, specialty
from participant where is_teacher or is_director order by display_name;
