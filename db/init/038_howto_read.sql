-- 038: раздел «Справка» — доступ по праву howto.read (V-010, Ш-49)
-- Решение заказчика 28.10.2026: справка — отдельный раздел, доступ пока только у админа,
-- остальным откроем после согласования текстов.

begin;

insert into rbac_capability (code, title, grp, description)
values ('howto.read', 'Справка по работе с ботом', 'help',
        'Читает пользовательские инструкции по ролям в разделе «Справка»')
on conflict (code) do nothing;

insert into rbac_role_capability (role, capability)
select 'admin', 'howto.read'
on conflict do nothing;

commit;

select role, capability from rbac_role_capability where capability = 'howto.read';
