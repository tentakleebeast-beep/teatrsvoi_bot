-- 036: обращения — только админу и дайджестом (V-005, Ш-45)
--
-- Решение Виктора 28.10.2026: «обращения только админу дайджестом».
-- Организатор больше не разбирает обратную связь: право feedback.triage
-- остаётся только у роли admin. Мгновенная рассылка feedback_new не ставится
-- (см. services/tool-api/app/main.py), вместо неё scripts/feedback_digest.py
-- раз в день в 11:00 кладёт одно сообщение со списком открытых обращений.
--
-- Уже поставленные в очередь мгновенные уведомления тем, у кого права больше
-- нет, отменяются: иначе после наката организатор всё равно получил бы старые.
-- Повторный накат безопасен.

begin;

delete from rbac_role_capability
 where role = 'org' and capability = 'feedback.triage';

update notification n
   set status = 'cancelled',
       last_error = 'мгновенная рассылка обращений отменена (Ш-45)'
 where n.kind = 'feedback_new' and n.status = 'scheduled'
   and not exists (
       select 1 from v_participant_capabilities c
        where c.participant_id = n.recipient_id
          and c.capability = 'feedback.triage');

commit;
