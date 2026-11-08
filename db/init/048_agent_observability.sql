-- 048: журнал разбора агентных сессий (карта A-5, docs/development/108 §6)
-- Зачем: по ответу агента видно только текст. Здесь ложатся шаги, инструменты,
-- аргументы (обезличенные), число шагов, токены, латентность и итоговый статус.
-- Старые поля не переименовываются: каскад интентов (014, docs/21) продолжает писать
-- в intent_log как раньше, новые поля у его строк остаются пустыми.
-- Ретенция общая с intent_log: scripts/intent_report.py --purge удаляет строку целиком
-- вместе с шагами (90 дней, scripts/cron/purge_intent.py).

begin;

alter table intent_log
  add column if not exists steps        jsonb,      -- шаги агента: step, tool, args, ok, error, latency_ms
  add column if not exists step_count   integer,    -- сколько шагов сделал агент
  add column if not exists agent_status text,       -- итог сессии: answer | proposal | refusal
  add column if not exists answer       text;       -- что человек увидел в ответе

-- Статус — закрытый список (как source): без него экран разбора покажет что угодно.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'intent_log_agent_status_chk') then
    alter table intent_log
      add constraint intent_log_agent_status_chk
      check (agent_status is null
             or agent_status in ('answer', 'proposal', 'refusal'));
  end if;
end $$;

-- Экран разбора читает только агентные сессии и только последние: частичный индекс
-- не растёт вместе с каскадом интентов, у которого шагов нет.
create index if not exists ix_intent_log_agent on intent_log (ts desc)
  where agent_status is not null;

comment on column intent_log.steps is 'Шаги агента (A-5): инструмент, обезличенные аргументы, результат, мс';
comment on column intent_log.agent_status is 'Итог агентной сессии: answer, proposal (нужна кнопка), refusal (ответила деградация)';
comment on column intent_log.answer is 'Ответ, который увидел человек: нужен для разбора вместе с вопросом';

-- Отчёты каскада (014) считают строки разбора интентов. Агентные сессии лежат в той же
-- таблице, но категорий не выбирают: без фильтра «модель разобрала» выросло бы на них,
-- и очередь доработки словаря показывала бы неправду.
create or replace view v_intent_gaps as
select date_trunc('day', ts)::date as day,
       count(*) as всего,
       count(*) filter (where source = 'llm') as разобрала_модель,
       count(*) filter (where source in ('buttons','human_new')) as спросили_человека,
       count(*) filter (where not resolved) as не_записано
from intent_log
where agent_status is null
group by 1
order by 1 desc;

create or replace view v_top_unmatched as
select text, count(*) as раз, max(ts) as последний_раз
from intent_log
where agent_status is null
  and source in ('buttons','human_new','failed')
group by text
order by раз desc, последний_раз desc
limit 100;

commit;

select count(*) as intent_log_rows from intent_log;
