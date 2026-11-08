-- 022: роли (персонажи) как псевдонимы человека в конкретном спектакле
-- Причина: люди говорят «окна для Наташи и Сэма в Жизни насекомых», имея в виду
-- Сашу Орлову и Витю Новикова. Роль — это псевдоним, привязанный к проекту.

begin;

-- роль живёт в проекте: «Сэм» существует только внутри «Жизни насекомых»
alter table person_alias
  add column if not exists project_id bigint references project(id) on delete cascade;

alter table person_alias drop constraint if exists person_alias_alias_kind_check;
alter table person_alias add constraint person_alias_alias_kind_check
  check (alias_kind in ('name', 'short', 'tg_username', 'payee', 'external', 'role'));

create index if not exists ix_person_alias_project on person_alias (project_id)
  where project_id is not null;

-- виды событий, для которых ищем окна (event_plan.kind сейчас без ограничения)
alter table event_plan drop constraint if exists event_plan_kind_check;
alter table event_plan add constraint event_plan_kind_check
  check (kind in ('rehearsal', 'show', 'training', 'rental', 'meeting', 'other'));

commit;

select alias_kind, count(*) from person_alias group by 1 order by 1;
