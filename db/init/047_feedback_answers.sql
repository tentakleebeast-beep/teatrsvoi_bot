-- 047: журнал разборов обратной связи (решение Виктора 30.10.2026)
--
-- Просьба: разбор обращений идёт отдельным заданием каждые 10 минут, ответ
-- автору дополнительно пишется в БД. `feedback` хранит только последний ответ
-- в `resolution`, поэтому по нему нельзя понять, разобрано ли уже обращение
-- и кто именно ответил. Журнал `feedback_answer` даёт:
--   * признак «разобрано» для очереди разбора (нет строки — не разобрано);
--   * историю ответов по одному обращению (доработка, вопрос, повторный разбор);
--   * ссылку на вопрос V-NNN в файле вопросов, чтобы не задавать его дважды.
--
-- Повторный накат безопасен: create if not exists + backfill с not exists.

begin;

create table if not exists feedback_answer (
    id          bigserial primary key,
    feedback_id bigint not null references feedback(id) on delete cascade,
    source      text not null default 'agent'
                check (source in ('agent', 'human')),
    status      text not null
                check (status in ('seen', 'planned', 'done', 'rejected')),
    text        text not null,
    question_id text,
    commit_sha  text,
    created_at  timestamptz not null default now()
);

create index if not exists feedback_answer_feedback_idx
    on feedback_answer (feedback_id, created_at desc);
create index if not exists feedback_answer_question_idx
    on feedback_answer (question_id) where question_id is not null;

-- Уже разобранные до журнала обращения: ответ есть в `resolution`, строки нет.
-- Источник — human: ответы ставил админ через POST /tools/feedback/decide.
insert into feedback_answer (feedback_id, source, status, text, created_at)
select f.id, 'human', f.status, f.resolution, coalesce(f.resolved_at, f.created_at)
  from feedback f
 where coalesce(f.resolution, '') <> ''
   and not exists (select 1 from feedback_answer a where a.feedback_id = f.id);

insert into migration_marker (key, note)
values ('047_feedback_answers', 'журнал разборов обратной связи')
on conflict (key) do nothing;

commit;

select count(*) as answers from feedback_answer;
