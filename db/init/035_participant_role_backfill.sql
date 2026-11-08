-- 035: регистрация через бота не создавала строку роли (обращение №24, INT-004)
--
-- `participant.role` — устаревшая колонка для совместимости; права считаются только
-- по `participant_role` (вьюха `v_participant_capabilities`). `/tools/participants/register`,
-- `/tools/participants/attach` и `/admin/bootstrap` вставляли участника, но не роль,
-- поэтому Марк Соколов и все, кто пришёл после него, видели «Возможностей: 0».
--
-- Код исправлен в этом же коммите; файл досоздаёт роли уже существующим записям.
-- Повторный накат безопасен: `on conflict do nothing`.

begin;

insert into participant_role (participant_id, role)
select id, role from participant
 where role in ('admin', 'org', 'troupe')
on conflict do nothing;

comment on column participant.role is
    'историческая колонка «основная роль»; права считаются по participant_role (см. 035)';

commit;
