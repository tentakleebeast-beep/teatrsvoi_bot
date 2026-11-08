-- 010: исправления по итогам живой работы
-- 1) kind в занятости: бот пишет busy/free, а констрейнт разрешал только declared/imported
alter table availability_hour drop constraint if exists availability_hour_kind_check;
update availability_hour set kind = 'busy' where kind = 'declared' and not is_free;
update availability_hour set kind = 'free' where kind = 'declared' and is_free;
alter table availability_hour add constraint availability_hour_kind_check
  check (kind in ('busy','free','preferred','declared','imported'));

-- 2) Маше вернуть роль организатора (сейчас в participant_role только troupe)
insert into participant_role (participant_id, role)
select id, 'org' from participant where display_name ilike '%Лебедева%'
on conflict do nothing;

update participant set role = 'org'
 where display_name ilike '%Лебедева%'
   and exists (select 1 from participant_role pr where pr.participant_id = participant.id and pr.role = 'org');

select p.display_name, p.role, array_agg(pr.role) roles
from participant p left join participant_role pr on pr.participant_id = p.id
where p.display_name ilike '%Лебедева%' group by 1,2;
