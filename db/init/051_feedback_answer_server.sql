-- 051: журнал разборов знает серверный разбор (карта t_17f137b8)
--
-- Правило владельца 02.11.2026: на сервере разбираются только вопросы и
-- благодарности, а баги и пожелания сервер лишь доставляет в dev. Чтобы это
-- было видно в журнале и чтобы мост выгрузки не считал доставленное
-- разобранным, нужен отдельный источник — `server`:
--   * `server` — запись серверного таймера (ответ автору или «передано в
--     разработку»); для bug/wish мост выгружает обращение, пока нет записи
--     с другим источником (`agent` ставит сам мост после выгрузки);
--   * `agent`, `human` — прежние значения, поведение не меняется.
--
-- Номер 050 занят в невлитой ветке feat/t_74ab8e2f-boot-beacon
-- (`050_webapp_boot_log.sql`), поэтому здесь 051, а не 050: дубль номера
-- всплыл бы при слиянии.
--
-- Повторный накат безопасен: drop constraint if exists + add.

begin;

alter table feedback_answer
    drop constraint if exists feedback_answer_source_check;

alter table feedback_answer
    add constraint feedback_answer_source_check
    check (source in ('agent', 'human', 'server'));

insert into migration_marker (key, note)
values ('051_feedback_answer_server', 'серверный разбор обращений: источник server')
on conflict (key) do nothing;

commit;

select distinct source from feedback_answer order by source;
