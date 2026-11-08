-- 061: роль «педагог» (master) — права, дисциплины и инвариант (Э1, docs/architecture/140).
--
-- Зачем. Педагог студии не мог пользоваться ботом по своей роли: значения `master`
-- не было ни в `participant_role.role`, ни в `rbac_role_capability`, поэтому
-- администратор не мог отметить человека как педагога, а человек — открыть сводную
-- занятость, свои отметки, поиск окон и назначение тренингов. Здесь роль получает
-- семь возможностей и связь с дисциплиной через `participant_discipline.relation='teacher'`
-- (одна или несколько дисциплин).
--
-- Деньги, рассылка, управление людьми и админ-панель роли не даются. Права — только
-- строки `rbac_role_capability`: отдельных списков ролей в коде не заводится
-- (`v_participant_capabilities` — единственное место, где решается «есть право или нет»).
-- `calendar.write` мастеру не выдаётся: маршруты событий проверяют `schedule.write`,
-- а `calendar.write` не проверяется нигде (140 §6.1) — мёртвое право обещать нельзя.
--
-- Автоматической выдачи роли здесь нет. Пункт 140 §5.1 («backfill мастеров из
-- `is_teacher`») не выполняется: §1 того же документа говорит «роль выдаёт
-- администратор», а выданная скриптом роль — это доступ без решения человека.
-- Люди с `is_teacher` (Крылов, Попова) получают роль только через карточку человека,
-- а `is_teacher = true` при выдаче ставит `POST /admin/grant`. Пункт 5 ниже нужен
-- для случая, когда роль уже выдана: состав тренинга (`_staff_by_rules`) до сих пор
-- смотрит на `is_teacher`, и мастер обязан в него попадать.
--
-- Номер 061: в `main` максимум занят 060 (`060_participant_role_revoked.sql`), 059 —
-- задокументированный буфер в `scripts/arch_drift.py` (`DOCUMENTED_GAPS[59]`), его не
-- трогаем. Пропуска между 060 и 061 нет.
--
-- Идемпотентно: повторный накат не меняет ни строк, ни определений. Прод обновляет
-- владелец отдельной картой деплоя, эта карта на прод не ходит.

begin;

-- 1. Роль `master` допускается в обоих местах: `participant.role` — витрина основной
--    роли, `participant_role.role` — источник прав. `invite_code.role` не меняется:
--    код приглашения не несёт дисциплины, а мастер без дисциплины — ошибка состояния.
alter table participant drop constraint if exists participant_role_check;
alter table participant add constraint participant_role_check
  check (role in ('admin', 'org', 'troupe', 'master'));

-- Имя ограничения на `participant_role` проект уже переименовывал (017 → `participant_role_check`,
-- 030 → `participant_role_role_check`), поэтому снимаем оба возможных имени: снятие
-- несуществующего ограничения — не ошибка, а забытое — роняет добавление нового.
alter table participant_role drop constraint if exists participant_role_check;
alter table participant_role drop constraint if exists participant_role_role_check;
alter table participant_role add constraint participant_role_check
  check (role in ('admin', 'org', 'troupe', 'master'));

-- 2. Семь возможностей педагога (140 §3). Остальные роли не трогаются.
insert into rbac_role_capability (role, capability) values
  ('master', 'schedule.read.all'),
  ('master', 'schedule.availability.self'),
  ('master', 'schedule.slots'),
  ('master', 'schedule.write'),
  ('master', 'notes.write'),
  ('master', 'files.access'),
  ('master', 'participants.view')
on conflict (role, capability) do nothing;

-- 3. Справочник «человек → дисциплины»: коды для правил и названия для подписи.
--    Пустой набор — пустой массив, а не NULL: агрегат с `filter` внутри и LEFT JOIN
--    дают `{}` там, где связей нет (иначе `array_agg` вернул бы `{NULL}`).
--    Строится по всем участникам, включая архивных: это справочник, а не список доступа.
create or replace view v_participant_disciplines as
select p.id as participant_id,
       p.display_name,
       coalesce(array_agg(d.code order by d.sort_order, d.code)
                  filter (where pd.relation = 'teacher'), '{}') as teacher_disciplines,
       coalesce(array_agg(d.name order by d.sort_order, d.code)
                  filter (where pd.relation = 'teacher'), '{}') as teacher_titles,
       coalesce(array_agg(d.code order by d.sort_order, d.code)
                  filter (where pd.relation = 'student'), '{}') as student_disciplines
  from participant p
  left join participant_discipline pd on pd.participant_id = p.id
  left join discipline d on d.id = pd.discipline_id
 group by p.id, p.display_name;

-- 4. Нарушители правила «мастер без дисциплины» (V-037 вариант а, 140 §2.2).
--    CHECK этого не поймает: связь живёт в другой таблице. View читает
--    `scripts/check_invariants.py` и тест; пустой результат — норма.
create or replace view v_master_without_discipline as
select r.participant_id, r.display_name
  from v_participant_roles r
 where 'master' = any(r.roles)
   and not exists (select 1
                     from participant_discipline pd
                    where pd.participant_id = r.participant_id
                      and pd.relation = 'teacher');

-- 5. Мастер — педагог для правил состава тренинга: `_staff_by_rules` (api/events.py)
--    читает и `participant_discipline`, и старый признак `is_teacher`.
update participant p
   set is_teacher = true
  from participant_role pr
 where pr.participant_id = p.id
   and pr.role = 'master'
   and pr.revoked_at is null
   and not p.is_teacher;

commit;
