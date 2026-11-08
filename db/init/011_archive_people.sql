-- 011: ушедшие из театра — в архив, без участия в агрегатах
-- Васильева Дарья, Соловьёв Максим, Семёнов Олег Викторович сейчас не в театре.
-- Принцип: человека не удаляем (история взносов и операций остаётся), но исключаем
-- из списков, счётчиков занятости и подбора окон.

begin;

update participant
   set status = 'archived',
       is_active = false,
       tg_chat_id = null
 where person_key in ('васильева', 'соловьёв', 'семёнов');

-- занятость ушедших в планировании бесполезна и искажает покрытие
delete from availability_hour a
 using participant p
 where p.id = a.participant_id and p.status = 'archived';

-- счётчики занятости считаем только по действующим
create or replace view v_availability_summary as
select a.day,
       a.hour,
       count(*) filter (where a.is_free) as free_cnt,
       count(*) filter (where not a.is_free) as busy_cnt,
       count(*) as known_cnt
from availability_hour a
join participant p on p.id = a.participant_id
where p.status <> 'archived'          -- pending тоже участвует: люди из файлов ещё не в боте
group by a.day, a.hour;

-- то же для сводки по людям: только действующие
create or replace view v_active_participants as
select id, display_name, person_key, role, status
from participant
where status <> 'archived';

commit;

select id, display_name, status, is_active from participant
 where person_key in ('васильева','соловьёв','семёнов') order by id;
select count(*) as активных from v_active_participants;
