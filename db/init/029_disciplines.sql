-- 029: дисциплины педагогов и участие в тренингах (уточнение 27.10.2026)
--
-- Педагог преподаёт конкретные дисциплины. Тренинг тоже имеет дисциплину,
-- и в состав попадают педагоги именно этой дисциплины.
--
-- Крылов (актёрское мастерство) на тренинге по музыке не нужен.
-- Попова (музыка) может прийти на актёрский тренинг как участник —
-- поэтому у связи есть признак: преподаёт или посещает.

begin;

create table if not exists discipline (
    id          bigserial primary key,
    code        text not null unique,
    name        text not null,
    sort_order  integer not null default 100
);

insert into discipline (code, name, sort_order) values
  ('acting', 'Актёрское мастерство', 10),
  ('music',  'Музыка', 20),
  ('dance',  'Танец', 30),
  ('speech', 'Сценическая речь', 40)
on conflict (code) do nothing;

-- связь человека и дисциплины: преподаёт или посещает
create table if not exists participant_discipline (
    participant_id bigint not null references participant(id) on delete cascade,
    discipline_id  bigint not null references discipline(id) on delete cascade,
    relation       text not null default 'student',   -- teacher | student
    primary key (participant_id, discipline_id)
);

alter table participant_discipline drop constraint if exists participant_discipline_relation_check;
alter table participant_discipline add constraint participant_discipline_relation_check
  check (relation in ('teacher', 'student'));

-- тренинг получает дисциплину
alter table event_plan add column if not exists discipline_id bigint references discipline(id);

commit;

insert into participant_discipline (participant_id, discipline_id, relation)
select p.id, d.id, 'teacher' from participant p, discipline d
where p.person_key like 'крылов%' and d.code in ('acting')
on conflict (participant_id, discipline_id) do update set relation = 'teacher';

insert into participant_discipline (participant_id, discipline_id, relation)
select p.id, d.id, 'teacher' from participant p, discipline d
where p.person_key like 'попова%' and d.code in ('music')
on conflict (participant_id, discipline_id) do update set relation = 'teacher';

-- Попова может заниматься актёрским как участник
insert into participant_discipline (participant_id, discipline_id, relation)
select p.id, d.id, 'student' from participant p, discipline d
where p.person_key like 'попова%' and d.code in ('acting')
on conflict (participant_id, discipline_id) do nothing;

select p.display_name, d.name, pd.relation
from participant_discipline pd
join participant p on p.id = pd.participant_id
join discipline d on d.id = pd.discipline_id
order by 1, 2;
