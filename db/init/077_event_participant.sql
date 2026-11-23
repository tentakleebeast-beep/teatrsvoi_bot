-- 077: личное время участия в событии (V-134, решение владельца 23.11.2026).
--
-- Зачем. У события был только общий интервал `starts_at`–`ends_at` и массив
-- `participant_ids` (001_schema.sql:197-212). Оба актёра в анкете 23.11.2026
-- просят другое: «со скольки я буду задействован, во сколько освобожусь (если
-- не на всё время репетиции)». Личное время — по желанию: пусто (`from_at` и
-- `to_at` is null) значит весь интервал события, поэтому обязательным поле не
-- становится и организатор не упирается в форму.
--
-- Что создаётся. Таблица состава события `event_participant`: пара
-- (event_id, participant_id) уникальна, внешние ключи с `on delete cascade` —
-- удаление события или человека уносит строки состава так же, как это делает
-- массив `participant_ids`.
--
-- Почему отдельная таблица, а не две колонки в `event_plan`. Время участия
-- относится к паре «событие — человек», а массив `participant_ids` пару не
-- адресует: колонки-массивы рядом с массивом id разъезжаются при любом
-- изменении состава. Порядок «с / до» проверяет сама база, а не только код.
--
-- Номер. В `main` максимум — 075 (`075_projects_money.sql`); 076 занял V-132
-- (`076_troupe_schedule_read.sql`, ветка wt/g1-troupe-schedule, карта t_ce317202),
-- поэтому свободный следующий — 077. Дубль номера всплыл бы при слиянии веток,
-- а порядок наката задаётся именем файла (docs/reports/115).
--
-- Идемпотентно: повторный накат не меняет ни строк, ни ограничений.
-- Откат (если понадобится): drop table if exists event_participant;
-- Прод обновляет владелец отдельной картой деплоя, эта карта на прод не ходит.

begin;

create table if not exists event_participant (
  event_id       bigint not null references event_plan(id) on delete cascade,
  participant_id bigint not null references participant(id) on delete cascade,
  from_at        timestamptz,
  to_at          timestamptz,
  constraint event_participant_pkey primary key (event_id, participant_id),
  -- пустое «с» или пустое «до» — «весь интервал события»; заданная пара
  -- обязана идти вперёд, иначе карточка показала бы «21:00–19:30»
  constraint event_participant_window_check check (to_at is null or to_at > from_at)
);

create index if not exists ix_event_participant_person
  on event_participant (participant_id);

commit;
