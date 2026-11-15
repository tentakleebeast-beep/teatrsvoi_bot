-- 064: педагог регистрируется своим кодом и выбирает дисциплину при онбординге.
--
-- Зачем. До этой миграции `master` кодом не выдавался (029, 061): у кода нет дисциплины,
-- поэтому человек сначала регистрировался как troupe/org, а админ потом выдавал роль
-- и предмет вручную. Решение владельца 15.11.2026: педагог — полноценная роль со своим
-- кодом, а дисциплину спрашиваем при регистрации (Актёрское мастерство, Музыка).
--
-- Код без дисциплины остаётся валидным: `discipline_code` в коде необязателен,
-- проверка «мастер без дисциплины» уже есть в core.discipline_warnings.

begin;

-- invite_code: роль master разрешена, дисциплина — необязательное поле
alter table invite_code drop constraint if exists invite_code_role_check;
alter table invite_code add constraint invite_code_role_check
  check (role in ('admin', 'org', 'troupe', 'master'));

alter table invite_code add column if not exists discipline_code text;

commit;
