-- 008: RBAC + capabilities
-- Роль больше не одна: у человека может быть несколько ролей (админ + организатор + студиец),
-- а доступ выдаётся не ролью, а конкретной возможностью (capability) на сценарий.

begin;

create table if not exists rbac_capability (
  code        text primary key,               -- 'budget.write'
  title       text not null,                  -- 'Записывать расходы и доходы'
  grp         text not null,                  -- 'budget' | 'schedule' | 'people' | 'system'
  description text
);

create table if not exists rbac_role_capability (
  role       text not null,
  capability text not null references rbac_capability(code) on delete cascade,
  primary key (role, capability)
);

create table if not exists participant_role (
  participant_id bigint not null references participant(id) on delete cascade,
  role           text not null check (role in ('admin','org','troupe','student')),
  granted_by     bigint references participant(id),
  granted_at     timestamptz not null default now(),
  primary key (participant_id, role)
);

-- точечные исключения: выдать или отобрать возможность лично
create table if not exists participant_capability_override (
  participant_id bigint not null references participant(id) on delete cascade,
  capability     text not null references rbac_capability(code) on delete cascade,
  allowed        boolean not null,
  reason         text,
  granted_by     bigint references participant(id),
  granted_at     timestamptz not null default now(),
  primary key (participant_id, capability)
);

-- ---------------------------------------------------------------- справочник возможностей
insert into rbac_capability (code, title, grp) values
  ('registration.code.issue',  'Выпускать коды приглашения',        'people'),
  ('registration.approve',     'Подтверждать заявки на доступ',     'people'),
  ('participants.view',        'Видеть список участников',          'people'),
  ('participants.manage',      'Менять роли и блокировать людей',   'people'),
  ('budget.read',              'Видеть бюджет театра',              'budget'),
  ('budget.read.self',         'Видеть свои взносы и долг',         'budget'),
  ('budget.write',             'Записывать расходы и доходы',       'budget'),
  ('budget.import',            'Импорт и реконсиляция бюджета',     'budget'),
  ('schedule.read.all',        'Видеть сводную занятость',          'schedule'),
  ('schedule.availability.self','Сообщать свою занятость',          'schedule'),
  ('schedule.slots',           'Подбирать окна для событий',        'schedule'),
  ('calendar.write',           'Создавать и отменять события',      'schedule'),
  ('notes.write',              'Вести заметки',                     'content'),
  ('files.access',             'Работать с файлами',                'content'),
  ('notify.send',              'Рассылать уведомления',             'system'),
  ('audit.view',               'Смотреть аудит',                    'system'),
  ('quota.manage',             'Настраивать лимиты AI',             'system'),
  ('system.health',            'Смотреть состояние сервисов',       'system')
on conflict (code) do nothing;

-- ---------------------------------------------------------------- роли -> возможности
insert into rbac_role_capability (role, capability) values
  ('admin', 'registration.code.issue'), ('admin','registration.approve'),
  ('admin', 'participants.view'), ('admin','participants.manage'),
  ('admin', 'budget.read'), ('admin','budget.read.self'), ('admin','budget.write'),
  ('admin', 'budget.import'),
  ('admin', 'schedule.read.all'), ('admin','schedule.availability.self'),
  ('admin', 'schedule.slots'), ('admin','calendar.write'),
  ('admin', 'notes.write'), ('admin','files.access'),
  ('admin', 'notify.send'), ('admin','audit.view'), ('admin','quota.manage'),
  ('admin', 'system.health'),

  ('org',   'registration.approve'),
  ('org',   'participants.view'),
  ('org',   'budget.read'), ('org','budget.read.self'), ('org','budget.write'),
  ('org',   'budget.import'),
  ('org',   'schedule.read.all'), ('org','schedule.availability.self'),
  ('org',   'schedule.slots'), ('org','calendar.write'),
  ('org',   'notes.write'), ('org','files.access'), ('org','notify.send'),
  ('org',   'system.health'),

  ('troupe', 'schedule.availability.self'), ('troupe','budget.read.self'),
  ('troupe', 'notes.write'), ('troupe','files.access'), ('troupe','system.health'),

  ('student','schedule.availability.self'), ('student','budget.read.self'),
  ('student','notes.write'), ('student','files.access')
on conflict do nothing;

-- ---------------------------------------------------------------- переносим текущие роли
insert into participant_role (participant_id, role)
select id, role from participant where role in ('admin','org','troupe','student')
on conflict do nothing;

-- ---------------------------------------------------------------- склейка дубля владельца
-- Раньше админ создался как отдельная запись («Виктор»), а в файлах он же «Иванов Виктор».
do $$
declare
  admin_id bigint;
  dup_id   bigint;
begin
  select id into admin_id from participant
   where role = 'admin' and status = 'active' order by id limit 1;
  select id into dup_id from participant
   where person_key = 'иванов' and tg_chat_id is null
     and id <> coalesce(admin_id, -1) limit 1;

  if admin_id is not null and dup_id is not null then
    delete from dues_charge d
     where d.participant_id = dup_id
       and exists (select 1 from dues_charge x
                    where x.participant_id = admin_id and x.period = d.period);
    update dues_charge set participant_id = admin_id where participant_id = dup_id;

    delete from availability_hour a
     where a.participant_id = dup_id
       and exists (select 1 from availability_hour x
                    where x.participant_id = admin_id and x.day = a.day and x.hour = a.hour);
    update availability_hour set participant_id = admin_id where participant_id = dup_id;

    update person_alias set participant_id = admin_id where participant_id = dup_id;
    update budget_tx set participant_id = admin_id where participant_id = dup_id;

    update participant set status = 'archived', is_active = false,
           display_name = display_name || ' (дубль)' where id = dup_id;
  end if;

  if admin_id is not null then
    -- освобождаем ключ, иначе unique(person_key) не даст переименовать
    update participant set person_key = null where id = dup_id;
    update participant set person_key = person_key || '_old'
     where person_key = 'иванов' and id <> admin_id;

    update participant
       set display_name = 'Иванов Виктор',
           person_key = 'иванов',
           status = 'active',
           is_active = true
     where id = admin_id;

    insert into participant_role (participant_id, role) values
      (admin_id,'admin'), (admin_id,'org'), (admin_id,'troupe'), (admin_id,'student')
    on conflict do nothing;

    insert into person_alias (participant_id, alias, alias_kind, source) values
      (admin_id,'иванов','name','manual'),
      (admin_id,'ивнов','name','manual'),
      (admin_id,'витя м','short','manual'),
      (admin_id,'виктор','short','manual')
    on conflict (alias) do nothing;
  end if;
end $$;

-- ---------------------------------------------------------------- вычисление возможностей
create or replace view v_participant_capabilities as
with direct as (
  select pr.participant_id, rc.capability
  from participant_role pr
  join rbac_role_capability rc on rc.role = pr.role
),
adds as (
  select participant_id, capability from participant_capability_override where allowed
),
removes as (
  select participant_id, capability from participant_capability_override where not allowed
)
select participant_id, capability from direct
union
select participant_id, capability from adds
except
select participant_id, capability from removes;

create or replace view v_participant_roles as
select p.id participant_id, p.display_name,
       array_agg(pr.role order by pr.role) roles
from participant p left join participant_role pr on pr.participant_id = p.id
group by p.id, p.display_name;

commit;

select * from v_participant_roles
 where participant_id = (select id from participant
                          where role = 'admin' and status = 'active' order by id limit 1);
select count(*) capabilities_for_admin from v_participant_capabilities
 where participant_id = (select id from participant
                          where role = 'admin' and status = 'active' order by id limit 1);
