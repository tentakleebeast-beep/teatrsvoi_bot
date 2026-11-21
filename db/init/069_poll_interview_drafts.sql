-- 069: четыре черновика анкеты оргсостава вместо одного на 46 вопросов (V-098, вариант «б»).
--
-- Основание: решение владельца 22.11.2026 (`docs/reports/74-questions.md`, V-098) —
-- вариант (б): четыре черновика 12/12/11/11, раскладка `docs/development/162-bot-polls.md`
-- §9; вопросы — `docs/development/161-google-interview-questionnaire.md` версия 8, дословно
-- (перенесены из 067 без правок). Спецификация прежнего одного черновика —
-- `docs/development/167-poll-interview-draft.md`; здесь её §9 «переключение (а) → (б)».
--
-- Почему новый номер, а не правка 067: 067 применён на проде (выкат v0.11.4, alembic-голова
-- 067; теперь 068), а применённые файлы не переписываются — иначе SHA в ревизии не сойдётся,
-- и на проде остался бы один черновик на 46 вопросов. 068 занят E11-3
-- (`068_org_without_approve.sql`), поэтому 069.
--
-- Что создаётся: четыре `poll` со `status='draft'` и вопросами своей роли —
--   «Анкета организаторам»  — 12 вопросов (161 §3), адресаты вручную: Виктор, Даина, Мария;
--   «Марии: деньги и соцсети» — 12 (161 §4), адресат Мария;
--   «Педагогам»             — 11 (161 §5), адресаты по роли `master`;
--   «Актёрам»               — 11 (161 §6), адресаты по роли `troupe`.
-- Три общих вопроса («что улучшить», «идеи по развитию», «чем полезна автоматизация»)
-- есть в каждом блоке: иначе ответы ролей не с чем сравнить (161 версия 8). Повтора
-- «четырьмя подряд», из-за которого вопрос V-098 и задавался, больше нет: человек получает
-- только свой блок. У Марии блоков два (организаторы и деньги) — общие вопросы она увидит
-- дважды; это отмечено в отчёте карты, правится галочкой на экране адресатов.
--
-- Адресаты и «кому результаты» заполняются сразу (тело карты V-098: «каждый черновик —
-- с адресатами своей роли»), но состав ШТАТНЫЙ, не окончательный: заморозка происходит
-- только на первой строке `poll_run`, до отправки адресаты правятся на экране
-- (`POST /webapp/polls/audience`). Отправок и приглашений файл не делает.
--
-- Одиночный черновик 067 удаляется, только если он не тронут: `status='draft'`, ни одной
-- строки `poll_run`, ровно 46 вопросов и все тексты совпадают с 161 (свёртка md5). Если
-- владелец успел поправить формулировки или запустить его — файл его НЕ трогает и пишет
-- notice: чужая правка не откатывается миграцией.
--
-- Идемпотентность: ключ `069_poll_interview_drafts` в `migration_marker` плюс совпадение
-- каждого `title`. `update` в файле нет, поэтому повторный прогон не плодит вопросы и не
-- откатывает правки владельца. Метка пишется только когда все четыре черновика в базе.
--
-- Автор — конкретный человек по `person_key`, а не литерал `id` (167 §3 D2): на свежей
-- базе из `db/init` участников нет, и литерал уронил бы накат. Запасное правило — первый
-- действующий администратор; нет ни того, ни другого — файл ничего не создаёт.

begin;

-- 1. Убрать одиночный черновик 067, если он не тронут (см. шапку). Условие по md5 —
--    защита от удаления правок владельца: любая правка текста меняет свёртку.
with удалён as (
  delete from poll p
   where p.title = 'Анкета оргсостава — черновик'
     and p.status = 'draft'
     and not exists (select 1 from poll_run r where r.poll_id = p.id)
     and (select count(*) from poll_question q where q.poll_id = p.id) = 46
     and (select md5(string_agg(q.text, E'\n' order by q.position))
            from poll_question q where q.poll_id = p.id) = '31d6fe6b0a23281dfde9187675927a4f'
  returning p.id
)
select count(*) from удалён;

-- §3 — 12 вопросов, адресаты вручную: Виктор, Даина, Мария
with владелец as (
  select coalesce(
           (select id from participant where person_key = 'мамлютов-виктор'),
           (select id from participant where role = 'admin' and status = 'active'
             order by id limit 1)
         ) as id
), черновик as (
  insert into poll (title, intro, status, anonymous, closes_at, created_by)
  select 'Анкета организаторам',
         'Как сейчас устроена работа в театре: 12 открытых вопросов организатору. Отвечать можно частями — прогресс сохраняется.',
         'draft', false, null, владелец.id
    from владелец
   where владелец.id is not null
     and not exists (select 1 from poll where title = 'Анкета организаторам')
     and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
  returning id
)
insert into poll_question (poll_id, position, text, kind, required, free_text, max_choices)
select черновик.id, в.position, в.text, 'text', true, false, null
  from черновик
 cross join (values
       (1, 'Расскажи, как выглядит твой обычный день в театре: с чего начинается, что делаешь первым?'),
       (2, 'Как часто ты обращаешься к общим файлам театра и зачем? Что ищешь в них чаще всего?'),
       (3, 'Нужно найти время для репетиции. Проведи меня по шагам: что смотришь сначала, что потом, кому пишешь или звонишь?'),
       (4, 'Что из этого ты решаешь сам, а что кто-то подтверждает за тебя?'),
       (5, 'Что чаще всего идёт не так в расписании и что ты делаешь, когда это случилось?'),
       (6, 'Кто и когда обновляет таблицу занятости? В ней есть колонка «Решение» — она нужна или её можно убрать?'),
       (7, 'Вспомни последнюю аренду: от заявки до оплаты — что и где ты записывал?'),
       (8, 'Если событие в календаре изменил кто-то другой, как ты об этом узнаёшь сейчас и что делаешь? Кого из организаторов бот может считать хозяином такого события?'),
       (9, 'Пять типов событий — аренда [А], спектакль [С], репетиция [Р], тренинг [Т], проект [П] — каким цветом пометить каждый изначально? Назови словами или номером палитры Google Календаря.'),
       (10, 'Что бы ты хотел улучшить в организации театра?'),
       (11, 'Какие есть идеи по развитию театра?'),
       (12, 'Чем бы тебе могла быть полезна автоматизация с помощью ИИ?')
       ) as в(position, text);

insert into poll_person (poll_id, participant_id, mode)
select п.id, ч.id, 'include'
  from poll п cross join participant ч
 where п.title = 'Анкета организаторам'
   and ч.person_key in ('мамлютов-виктор', 'тишина-даина', 'загаринская-мария')
   and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
on conflict do nothing;

insert into poll_viewer (poll_id, participant_id)
select п.id, ч.id
  from poll п cross join participant ч
 where п.title = 'Анкета организаторам'
   and ч.person_key in ('мамлютов-виктор', 'тишина-даина', 'загаринская-мария')
   and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
on conflict do nothing;

-- §4 — 12 вопросов, адресат — Мария
with владелец as (
  select coalesce(
           (select id from participant where person_key = 'мамлютов-виктор'),
           (select id from participant where role = 'admin' and status = 'active'
             order by id limit 1)
         ) as id
), черновик as (
  insert into poll (title, intro, status, anonymous, closes_at, created_by)
  select 'Марии: деньги и соцсети',
         'Как сейчас устроены деньги и соцсети театра: 12 открытых вопросов. Отвечать можно частями — прогресс сохраняется.',
         'draft', false, null, владелец.id
    from владелец
   where владелец.id is not null
     and not exists (select 1 from poll where title = 'Марии: деньги и соцсети')
     and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
  returning id
)
insert into poll_question (poll_id, position, text, kind, required, free_text, max_choices)
select черновик.id, в.position, в.text, 'text', true, false, null
  from черновик
 cross join (values
       (1, 'Проведи меня по бюджету месяца: с чего начинаешь и чем заканчиваешь?'),
       (2, 'Что в листе трогать нельзя, иначе всё поедет, — а что мешает и хочется переделать?'),
       (3, 'Расскажи на примере последней траты: где и как ты её записала? Статью пишешь словами или выбираешь из списка? Есть ли у тебя свой список статей?'),
       (4, 'Как ты понимаешь, сколько осталось на конец месяца?'),
       (5, 'Кто отмечает, что студиец заплатил взнос, и когда ты об этом узнаёшь? Что делать с тем, кто не заплатил: напоминать ему, показывать тебе список, ничего?'),
       (6, 'Как ты считаешь оплату педагогам: из чего складывается сумма и когда ты её считаешь?'),
       (7, 'Где вы публикуете и где лежат материалы для постов — афиши, тексты, фото?'),
       (8, 'Как ты понимаешь, что пост готов к публикации?'),
       (9, 'Что из твоей работы бот мог бы делать за тебя? О каких сроках тебе важно получать напоминание?'),
       (10, 'Что бы ты хотел улучшить в организации театра?'),
       (11, 'Какие есть идеи по развитию театра?'),
       (12, 'Чем бы тебе могла быть полезна автоматизация с помощью ИИ?')
       ) as в(position, text);

insert into poll_person (poll_id, participant_id, mode)
select п.id, ч.id, 'include'
  from poll п cross join participant ч
 where п.title = 'Марии: деньги и соцсети'
   and ч.person_key in ('загаринская-мария')
   and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
on conflict do nothing;

insert into poll_viewer (poll_id, participant_id)
select п.id, ч.id
  from poll п cross join participant ч
 where п.title = 'Марии: деньги и соцсети'
   and ч.person_key in ('мамлютов-виктор', 'загаринская-мария')
   and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
on conflict do nothing;

-- §5 — 11 вопросов, адресаты по роли master
with владелец as (
  select coalesce(
           (select id from participant where person_key = 'мамлютов-виктор'),
           (select id from participant where role = 'admin' and status = 'active'
             order by id limit 1)
         ) as id
), черновик as (
  insert into poll (title, intro, status, anonymous, closes_at, created_by)
  select 'Педагогам',
         'Как сейчас проходит твой день с тренингом: 11 открытых вопросов. Отвечать можно частями — прогресс сохраняется.',
         'draft', false, null, владелец.id
    from владелец
   where владелец.id is not null
     and not exists (select 1 from poll where title = 'Педагогам')
     and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
  returning id
)
insert into poll_question (poll_id, position, text, kind, required, free_text, max_choices)
select черновик.id, в.position, в.text, 'text', true, false, null
  from черновик
 cross join (values
       (1, 'Расскажи, как проходит твой день с тренингом: что делаешь до него и после?'),
       (2, 'Как ты показываешь, когда занят, а когда свободен?'),
       (3, 'Когда ты открываешь расписание и что в нём ищешь?'),
       (4, 'Что нужно подготовить к тренингу из файлов и музыки? Куда ты складываешь новое?'),
       (5, 'Как ты узнаёшь, сколько тебе заплатят за месяц? Из чего складывается сумма?'),
       (6, 'Что тебе нужно узнать из бота первым делом, когда ты его открываешь?'),
       (7, 'О каких изменениях в расписании тебе важно узнавать сразу, а о каких — не надо?'),
       (8, 'Что в твоей работе бот делать не должен ни при каких условиях?'),
       (9, 'Что бы ты хотел улучшить в организации театра?'),
       (10, 'Какие есть идеи по развитию театра?'),
       (11, 'Чем бы тебе могла быть полезна автоматизация с помощью ИИ?')
       ) as в(position, text);

insert into poll_audience (poll_id, kind, role)
select п.id, 'role', ч.role
  from poll п cross join participant ч
 where п.title = 'Педагогам'
   and ч.role = 'master'
   and ч.status = 'active'
   and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
   and not exists (select 1 from poll_audience а
                    where а.poll_id = п.id and а.kind = 'role' and а.role = ч.role)
group by п.id, ч.role;

insert into poll_viewer (poll_id, participant_id)
select п.id, ч.id
  from poll п cross join participant ч
 where п.title = 'Педагогам'
   and ч.person_key in ('мамлютов-виктор', 'наквасина-света', 'трегубов-виктор')
   and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
on conflict do nothing;

-- §6 — 11 вопросов, адресаты по роли troupe
with владелец as (
  select coalesce(
           (select id from participant where person_key = 'мамлютов-виктор'),
           (select id from participant where role = 'admin' and status = 'active'
             order by id limit 1)
         ) as id
), черновик as (
  insert into poll (title, intro, status, anonymous, closes_at, created_by)
  select 'Актёрам',
         'Как сейчас проходят репетиции и тренинги: 11 открытых вопросов. Отвечать можно частями — прогресс сохраняется.',
         'draft', false, null, владелец.id
    from владелец
   where владелец.id is not null
     and not exists (select 1 from poll where title = 'Актёрам')
     and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
  returning id
)
insert into poll_question (poll_id, position, text, kind, required, free_text, max_choices)
select черновик.id, в.position, в.text, 'text', true, false, null
  from черновик
 cross join (values
       (1, 'Как ты узнаёшь, когда у тебя репетиция или тренинг?'),
       (2, 'Где и как ты отмечаешь, что свободен или занят?'),
       (3, 'Что тебе нужно узнать или найти перед репетицией или тренингом? Отличается ли подготовка к одному и к другому?'),
       (4, 'Что чаще всего идёт не так с репетициями и тренингами и что ты делаешь, когда это случилось?'),
       (5, 'Если репетиция или тренинг отменились, как ты об этом узнаёшь и что делаешь дальше?'),
       (6, 'О чём тебе важно получать сообщение от театра, а о чём — не надо? Чем сообщение о тренинге отличается от сообщения о репетиции?'),
       (7, 'Какие файлы театра тебе нужны чаще всего и зачем? К тренингу и к репетиции нужны разные файлы?'),
       (8, 'Что тебе важно знать о репетиции или тренинге, кроме даты и времени? Для репетиции — какой спектакль и что за роль, для тренинга — какая дисциплина и кто ведёт?'),
       (9, 'Что бы ты хотел улучшить в организации театра?'),
       (10, 'Какие есть идеи по развитию театра?'),
       (11, 'Чем бы тебе могла быть полезна автоматизация с помощью ИИ?')
       ) as в(position, text);

insert into poll_audience (poll_id, kind, role)
select п.id, 'role', ч.role
  from poll п cross join participant ч
 where п.title = 'Актёрам'
   and ч.role = 'troupe'
   and ч.status = 'active'
   and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
   and not exists (select 1 from poll_audience а
                    where а.poll_id = п.id and а.kind = 'role' and а.role = ч.role)
group by п.id, ч.role;

insert into poll_viewer (poll_id, participant_id)
select п.id, ч.id
  from poll п cross join participant ч
 where п.title = 'Актёрам'
   and ч.person_key in ('мамлютов-виктор')
   and not exists (select 1 from migration_marker where key = '069_poll_interview_drafts')
on conflict do nothing;

insert into migration_marker (key, note)
select '069_poll_interview_drafts', 'четыре черновика анкеты оргсостава: 12/12/11/11'
 where (select count(*) from poll where title in ('Анкета организаторам', 'Марии: деньги и соцсети', 'Педагогам', 'Актёрам')) = 4
on conflict (key) do nothing;

do $$
begin
  if not exists (select 1 from poll where title = 'Анкета организаторам') then
    raise notice '069: владельца и действующего администратора нет — черновики не созданы';
  end if;
  if exists (select 1 from poll where title = 'Анкета оргсостава — черновик') then
    raise notice '069: одиночный черновик 067 не удалён (есть прохождения или правки) — удалите его в конструкторе';
  end if;
end $$;

commit;
