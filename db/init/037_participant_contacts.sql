-- 037: контактные и творческие поля профиля (V-009, обращение №17, Ш-48)
--
-- Решение Виктора 28.10.2026: знакомство спрашивает имя, телефон, день рождения
-- (если ещё не известны), обычное время в пути, спектакли, роли и интересные
-- тренинги; тариф не спрашивает — его ставит организатор после договорённости.
-- Телефон и день рождения нужны для связи и поздравлений, «спектакли/роли/
-- тренинги» — свободный текст: структурные роли по спектаклям заполняются
-- в анкете Mini App, где выбирается проект.
--
-- Повторный накат безопасен.

begin;

alter table participant add column if not exists phone text;
alter table participant add column if not exists birth_date date;
alter table participant add column if not exists plays text;
alter table participant add column if not exists roles_note text;
alter table participant add column if not exists interests text;

alter table participant drop constraint if exists participant_phone_check;
alter table participant add constraint participant_phone_check
  check (phone is null or phone ~ '^\+?[0-9]{10,15}$');

alter table participant drop constraint if exists participant_birth_date_check;
alter table participant add constraint participant_birth_date_check
  check (birth_date is null or birth_date between date '1900-01-01' and date '2020-12-31');

comment on column participant.phone is
  'Телефон для связи, только цифры с необязательным + (Ш-48)';
comment on column participant.birth_date is
  'День рождения — для поздравлений и возраста (Ш-48)';
comment on column participant.plays is
  'Спектакли, в которых участвует, свободным текстом (Ш-48)';
comment on column participant.roles_note is
  'Роли свободным текстом; структурные роли — person_alias.alias_kind=role (Ш-48)';
comment on column participant.interests is
  'Интересные тренинги и темы, свободным текстом (Ш-48)';

commit;

select count(*) as людей, count(phone) as с_телефоном, count(birth_date) as с_днём
from participant;
