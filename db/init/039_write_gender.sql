-- 039: род для канонической записи занятости в лист (FR-C9).
-- В листе пишут «занята до 19:00» и «свободна с 18:00» — 20 клеток из 722.
-- Без рода бот, записывая неделю из Mini App, менял бы окончание человеку,
-- а такую правку в файле заметят сразу.
begin;

alter table participant
  add column if not exists write_gender text not null default 'm';

alter table participant drop constraint if exists participant_write_gender_check;
alter table participant add constraint participant_write_gender_check
  check (write_gender in ('m', 'f'));

commit;

select column_name, column_default
  from information_schema.columns
 where table_name = 'participant' and column_name = 'write_gender';
