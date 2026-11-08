-- 031: обратная связь от участников + личный манифест для AI
--
-- Обратная связь — то, чего не хватало: люди видели дефекты и молчали,
-- потому что сообщить было некуда. Три входа: свободный текст в чате,
-- кнопка меню, форма Mini App.

begin;

create table if not exists feedback (
    id             bigserial primary key,
    participant_id bigint references participant(id),
    kind           text not null default 'bug'
                   check (kind in ('bug', 'wish', 'question', 'praise')),
    text           text not null,
    context        jsonb,                  -- где человек был: экран, последние действия
    status         text not null default 'new'
                   check (status in ('new', 'seen', 'planned', 'done', 'rejected')),
    resolution     text,
    created_at     timestamptz not null default now(),
    resolved_at    timestamptz
);

create index if not exists feedback_status_idx on feedback (status, created_at desc);
create index if not exists feedback_participant_idx on feedback (participant_id, created_at desc);

-- личный манифест: как AI должен говорить с этим человеком
alter table participant add column if not exists ai_manifest text;

insert into rbac_capability (code, title, grp) values
    ('feedback.send',   'Сообщать о проблемах и пожеланиях', 'Обратная связь'),
    ('feedback.triage', 'Разбирать обратную связь',          'Обратная связь')
on conflict (code) do nothing;

insert into rbac_role_capability (role, capability) values
    ('admin',  'feedback.send'), ('org', 'feedback.send'), ('troupe', 'feedback.send'),
    ('admin',  'feedback.triage'), ('org', 'feedback.triage')
on conflict do nothing;

commit;

select code from rbac_capability where code like 'feedback%';
