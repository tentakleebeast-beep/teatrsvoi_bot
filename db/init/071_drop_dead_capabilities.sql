-- 071: мёртвые права сняты с ролей (V-121, docs/development/188 §6).
--
-- Зачем. Три права в матрице не имели ни одного входа и обещали недоступное:
--   * `system.health` у `org` и `troupe` — оба входа (кнопка «Сервисы» в панели бота,
--     экран `/webapp/services`) закрыты `admin.panel`, которого у этих ролей нет
--     (`keyboards.py:PANEL_ENTRY_CAPS`, `webapp.py:ADMIN_BLOCKS` → admin.panel);
--   * `quota.manage` у `admin` — ни экрана, ни действия: право встречалось только
--     в подписи `texts.CAP_TITLES`, то есть ни на что не влияло.
-- Решение владельца 22.11.2026 (V-121, вариант а): снять права с ролей, чтобы матрица
-- не обещала недоступное. Сами коды в справочнике `rbac_capability` остаются: это
-- словарь возможностей, а не выдача; точечная выдача через
-- `participant_capability_override` по-прежнему возможна и пишется в аудит.
--
-- У `admin` `system.health` сохраняется: кнопка «Сервисы» и экран сервисов открыты
-- именно этой роли.
--
-- Номер. Максимум в `main` — 070 (`070_personal_invite_code.sql`); 071 следующий
-- свободный. Пропусков нет.
--
-- Идемпотентно: повторный накат не меняет ни строк, ни чисел. Прод обновляет владелец
-- отдельной картой деплоя, эта карта на прод не ходит.

begin;

delete from rbac_role_capability
 where role in ('org', 'troupe')
   and capability = 'system.health';

delete from rbac_role_capability
 where role = 'admin'
   and capability = 'quota.manage';

commit;

select role, count(*) capabilities
  from rbac_role_capability
 where role in ('admin', 'org', 'troupe')
 group by role
 order by role;