-- 041: person_key — человекочитаемый ключ целиком, а не первое слово (INT-006).
-- Было: «Иванов Виктор» → «иванов», и в базе оказывались ключи вида
-- «дарья васильева» (с пробелом) и «олег викторович». Ключ — это идентификатор,
-- который человек узнаёт в лицо, и одновременно имя файла личных настроек,
-- поэтому он приводится к одному виду: строчные буквы, цифры и дефис.
--
-- Правило то же, что в services/tool-api/app/person_key.py (сверяется тестом).
begin;

-- 1. Освобождаем уникальный индекс и убираем старые ключи: пересчёт — по всем строкам.
update participant set person_key = null;

-- 2. Новый ключ: нижний регистр, ё → е, скобки выбрасываем, прочее — в дефис.
--    Совпадение остаётся за живой записью (active → pending → archived, затем id),
--    дубли получают «-архив», второй и следующий дубль — «-архив-2», «-архив-3».
--
--    Нормализация и разбор совпадений идут одним проходом: индекс уникальный,
--    и отдельный шаг «сначала всем одинаковый ключ, потом развести» падает.
with base as (
  select id, status,
         trim(both '-' from
           regexp_replace(
             regexp_replace(lower(translate(display_name, 'ёЁ', 'еЕ')), '\([^)]*\)', '', 'g'),
             '[^a-zа-я0-9]+', '-', 'g')) as key_base
    from participant
), ranked as (
  select id, key_base,
         row_number() over (
           partition by key_base
           order by case status when 'active' then 0 when 'pending' then 1 else 2 end, id
         ) as place
    from base
)
update participant p
   set person_key = case
         when r.place = 1 then r.key_base
         when r.place = 2 then r.key_base || '-архив'
         else r.key_base || '-архив-' || r.place
       end
  from ranked r
 where p.id = r.id and r.key_base <> '';

-- 3. Формат: без пробелов и служебных символов, иначе ключ ломает имя файла.
alter table participant drop constraint if exists participant_person_key_format;
alter table participant add constraint participant_person_key_format
  check (person_key is null or person_key ~ '^[a-zа-я0-9]+(-[a-zа-я0-9]+)*$');

comment on column participant.person_key is
  'Человекочитаемый ключ: «иванов-виктор»; имя файла личных настроек';

commit;

select id, display_name, person_key
  from participant
 where person_key is not null
 order by person_key;
