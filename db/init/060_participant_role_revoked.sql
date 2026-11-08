-- 060: отзыв роли как состояние строки, а не удаление (Ш-07, Л4/Л5, FR-A6).
--
-- Зачем. `docs/architecture/11` §5 п.1: строки не удаляются, только помечаются.
-- До этой миграции отзыв роли был `delete from participant_role` в `POST /admin/grant`
-- (`enabled = false`): история выдачи права терялась, «кто и когда снял» нигде не
-- оставалось, хотя действие пишется в аудит. Теперь у строки есть состояние, а вьюхи
-- прав считают только неотозванные строки — единственное место, где решается «есть
-- право или нет».
--
-- Поля состояния, а не отдельная таблица: ключ роли и так (participant_id, role),
-- поэтому повторная выдача — это `revoked_at = null`, а не вторая строка.
--
-- Номер. На 04.11.2026 в `main` уже 056 (`056_notification_defaults.sql`, Ш-11) и
-- 058 (`058_feedback_wish_fl.sql`, Ш-12) — максимум занят. 059 оставлен буфером:
-- параллельные ветки берут следующий свободный номер, и два файла с одним номером
-- роняют `check_registers` (`test_настоящие_миграции_без_дублей_номеров`). Пропуск
-- 059 записан как безопасный в `scripts/arch_drift.py` (`DOCUMENTED_GAPS[59]`);
-- снимать эту запись будет тот, кто займёт 059.
--
-- Прод. Миграция идемпотентна: повторный выкат ничего не меняет, старые строки
-- остаются с `revoked_at = null`, то есть действующими. Применяет владелец
-- отдельной картой деплоя, эта карта на прод не ходит.

begin;

alter table participant_role add column if not exists revoked_at timestamptz;
alter table participant_role add column if not exists revoked_by bigint references participant(id);
alter table participant_role add column if not exists revoke_reason text;

create or replace view v_participant_capabilities as
with direct as (
  select pr.participant_id, rc.capability
  from participant_role pr
  join rbac_role_capability rc on rc.role = pr.role
  where pr.revoked_at is null
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

-- `array_agg` по отозванным строкам дал бы {NULL}: роль снята, а в списке осталась.
-- Поэтому фильтр стоит внутри агрегата, а не в WHERE — иначе человек без единой
-- действующей роли выпал бы из вьюхи совсем (LEFT JOIN + фильтр в WHERE = INNER JOIN).
create or replace view v_participant_roles as
select p.id participant_id, p.display_name,
       array_agg(pr.role order by pr.role) filter (where pr.revoked_at is null) roles
from participant p left join participant_role pr on pr.participant_id = p.id
group by p.id, p.display_name;

commit;
