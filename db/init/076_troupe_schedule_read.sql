-- 076: роль `troupe` читает расписание студии (решение владельца V-132 от 23.11.2026).
--
-- Зачем. Актёр не мог увидеть ни расписание, ни своё участие: у роли `troupe` не было
-- ни `schedule.read.all`, ни `schedule.write` (`db/init/008_rbac_capabilities.sql:82-83`),
-- поэтому `GET /tools/calendar/events` (право `schedule.read.all`) отвечал 403, а карточка
-- события `GET /tools/events/{id}` была закрыта ещё и правом записи. Из анкеты актёров
-- 23.11.2026: один узнаёт о репетиции из чата ВК, другой из таблицы — бот не источник
-- правды (`docs/reports/194-anketa-aktyory-razbor.md` §6).
--
-- Что выдаётся. Только чтение: `schedule.read.all` роли `troupe`. Право записи
-- (`schedule.write`, `calendar.write`, `schedule.slots`) не выдаётся: сценарии записи,
-- экран «Сверка» и настройка типов событий остаются за `schedule.write`.
-- Личные поля других людей (телефон, взносы, дорога, день рождения, заметки) в ответах
-- расписания не отдаются — это проверяет `tests/integration/test_troupe_schedule.py`.
--
-- Номер. Максимум в `main` — 075 (`075_projects_money.sql`); 074 (`074_calendar_event_types.sql`,
-- типы и цвета событий E5-4) и 075 (проекты E6-2) того же дня заняли номера раньше нас,
-- поэтому свободный следующий — 076. Дубль номера всплыл бы при слиянии веток, а порядок
-- наката задаётся именем файла: две миграции с одним номером дали бы разную схему
-- на чистой базе и на проде (docs/reports/115).
--
-- Идемпотентно: повторный накат не меняет ни строк, ни чисел (`on conflict do nothing`).
-- Откат (если понадобится): delete from rbac_role_capability
--   where role = 'troupe' and capability = 'schedule.read.all';
-- Прод обновляет владелец отдельной картой деплоя, эта карта на прод не ходит.

begin;

insert into rbac_role_capability (role, capability)
values ('troupe', 'schedule.read.all')
on conflict do nothing;

commit;

select role, count(*) capabilities
  from rbac_role_capability
 where role = 'troupe'
 group by role;
