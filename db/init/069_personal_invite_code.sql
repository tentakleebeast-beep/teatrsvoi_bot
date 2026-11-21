-- 069: код приглашения персональный + право registration.approve снимается.
--
-- Причина: решение владельца 21.11.2026 (V-116 б, V-117 а, V-118 а) — код
-- выписывается на конкретного человека, запись заводится на экране «Приглашения»
-- (docs/development/182 §4.3), заявок в боте больше нет ни в одной ветке (§4.1),
-- поэтому право подтверждения не выдаётся ни одной роли.
--
-- Что делает:
--   1. invite_code.participant_id — на кого выписан код; null у старых кодов
--      (они перестают приниматься, check_code отвечает code_not_personal);
--   2. снимает выдачу registration.approve у всех ролей. Строка справочника
--      rbac_capability остаётся: на неё смотрят применённые ревизии, удаление
--      описания права — отдельная миграция с неизвестной ценой (§4.1).
--
-- Данные не переносятся: на 21.11.2026 participant.status = 'pending' — 0
-- (три записи 15, 20, 23 подтверждены через /admin/approve). Скопом переводить
-- pending в active нельзя: это выдало бы доступ без проверки (§4.2).
--
-- Идемпотентно: add column if not exists, create index if not exists, delete по
-- паре (role, capability); повторный накат не падает.
--
-- Откат: alter table invite_code drop column participant_id;
--        выдать гранты обратно (008:63 для admin, 008:73 для org) — только
--        вручную, автооткат данные не вернёт.
begin;
alter table invite_code add column if not exists participant_id bigint
  references participant(id) on delete set null;
create index if not exists invite_code_participant_idx on invite_code (participant_id);
delete from rbac_role_capability where capability = 'registration.approve';
commit;