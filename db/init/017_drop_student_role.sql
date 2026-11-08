-- 017: убираем роль student — остаются admin, org, troupe
-- Решение заказчика 26.10.2026: студенты курсов пока не отделяются от актёров;
-- вернём роль, если появится потребность. Текущие носители student переводятся в troupe
-- (их набор возможностей — подмножество troupe: troupe = student + system.health).

begin;

-- 1. всем, у кого была роль student, добавляем troupe
insert into participant_role (participant_id, role)
select distinct participant_id, 'troupe'
  from participant_role where role = 'student'
on conflict do nothing;

-- 2. снимаем student
delete from participant_role where role = 'student';

-- 3. основное поле роли
update participant set role = 'troupe' where role = 'student';

-- 4. возможности роли
delete from rbac_role_capability where role = 'student';

-- 5. убираем значение из CHECK
alter table participant_role drop constraint if exists participant_role_check;
alter table participant_role add constraint participant_role_check
  check (role in ('admin', 'org', 'troupe'));

commit;

select role, count(*) from participant_role group by 1 order by 1;
select role, count(*) from rbac_role_capability group by 1 order by 1;
