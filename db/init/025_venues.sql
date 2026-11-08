-- 025: пространства (залы) и параллельные мероприятия
--
-- Решение 27.10.2026: репетиция на сцене и разбор роли в гримёрке не мешают
-- друг другу; аренда на сцене не блокирует хоз.работы в подсобках.
-- Значит, занятость считается по пространству, а не по студии целиком.
--
-- Буферы (уборка, перестановка) НЕ вводим: по решению заказчика уборка
-- и подготовка обычно входит во время аренды.

begin;

create table if not exists venue (
    id           bigserial primary key,
    code         text not null unique,          -- stage | dressing | backstage
    name         text not null,                 -- «Сцена», «Гримёрка», «Подсобки»
    kind         text not null default 'room',  -- room | stage | outdoor
    calendar_id  text,                          -- свой календарь, если есть
    is_active    boolean not null default true,
    sort_order   integer not null default 100,
    created_at   timestamptz not null default now()
);

comment on column venue.calendar_id is
  'Если задан — занятость берётся через freebusy этого календаря; '
  'если нет — из событий общего календаря по svoi_venue_id';

insert into venue (code, name, kind, sort_order) values
  ('stage',     'Сцена',     'stage', 10),
  ('dressing',  'Гримёрка',  'room',  20),
  ('backstage', 'Подсобки',  'room',  30)
on conflict (code) do nothing;

-- основное пространство: события без явного зала считаются событиями сцены
create table if not exists app_setting (
    key        text primary key,
    value      text not null,
    updated_at timestamptz not null default now()
);

insert into app_setting (key, value) values ('default_venue_code', 'stage')
on conflict (key) do nothing;

commit;

select code, name, is_active from venue order by sort_order;
