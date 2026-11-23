-- 072: календарь события + режим приложения Google.
--
-- Причина: спецификация процесса «файлы + календарь + бот»
-- (docs/development/186 §5.5, §2.3, дизайн 185 D5/D7). Бот создаёт событие только
-- в черновом календаре, но в event_plan не было колонки, где этот календарь
-- записан, — отличить «своё черновое» от «чужого боевого» можно было только лишним
-- запросом в Google, а отказ `not_bot_event` решается по базе. Режим приложения
-- (`transitional`/`target`) тоже хранится в app_setting, и код обязан считать
-- отсутствие строки переходным режимом, а не падать.
--
-- Номер: 071 занят файлом 071_drop_dead_capabilities.sql (влит в main); в невлитых
-- ветках номера 072 нет — проверено по всем worktree 23.11.2026.
--
-- Что делает:
--   1. event_plan.gcal_calendar_id — календарь, в котором бот создал событие;
--   2. app_setting('google.mode','transitional') — режим по умолчанию.
--
-- Данные не переносятся: существующие строки event_plan получают null, что и значит
-- «бот не знает календаря события»; google_mode() отсутствие строки считает
-- transitional, поэтому и на базе до наката поведение не меняется.
--
-- Идемпотентно: add column if not exists, insert ... on conflict do nothing.
--
-- Откат: alter table event_plan drop column gcal_calendar_id;
--        delete from app_setting where key = 'google.mode';
begin;
alter table event_plan add column if not exists gcal_calendar_id text;
insert into app_setting (key, value) values ('google.mode', 'transitional')
on conflict (key) do nothing;
commit;
