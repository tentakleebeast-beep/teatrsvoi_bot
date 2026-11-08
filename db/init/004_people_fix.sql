-- 004: уточнения по людям (со слов заказчика)
--   * «Олег Викторов» = Семёнов Олег Викторович, приглашённый актёр «И всё же...» (по Бунину)
--   * Тарасова Алёна — бывший участник, Морозова Алёна — действующий

begin;

update participant
   set display_name = 'Семёнов Олег Викторович',
       person_key   = 'семёнов',
       is_active    = true,
       status       = coalesce(nullif(status, 'pending'), 'pending')
 where display_name in ('Олег Викторов', 'Олег Викторович Семёнов');

insert into person_alias (participant_id, alias, alias_kind, source)
select id, a, 'short', 'manual'
from participant p, (values ('олег викторов'), ('олег'), ('семёнов'), ('ов'), ('олег викторович')) as t(a)
where p.display_name = 'Семёнов Олег Викторович'
on conflict (alias) do nothing;

-- бывший участник: не удаляем историю, но исключаем из активных
insert into participant (tg_chat_id, display_name, person_key, role, status, is_active)
select null, 'Тарасова Алёна', 'тарасова', 'student', 'archived', false
where not exists (select 1 from participant where person_key = 'тарасова');

insert into person_alias (participant_id, alias, alias_kind, source)
select id, 'тарасова алена', 'name', 'manual' from participant
where person_key = 'тарасова' on conflict (alias) do nothing;

commit;

select id, display_name, person_key, role, status, is_active from participant order by is_active desc, id;
