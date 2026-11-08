-- 014: журнал разбора интентов и кандидаты в категории (docs/development/21)
-- Нужен, чтобы видеть, где правила молчат, и не давать модели придумывать категории.

begin;

create table if not exists intent_log (
  id              bigserial primary key,
  ts              timestamptz not null default now(),
  chat_id         bigint,
  participant_id  bigint references participant(id),
  text            text not null,
  intent_rules    text,
  intent_llm      text,
  category_rules  text,
  category_llm    text,
  category_final  text,
  source          text not null check (source in ('rules','llm','buttons','human_new','failed')),
  confidence      numeric(4,3),
  resolved        boolean not null default false,
  tokens          integer,
  latency_ms      integer
);

create index if not exists ix_intent_log_ts on intent_log (ts desc);
create index if not exists ix_intent_log_source on intent_log (source);
create index if not exists ix_intent_log_unresolved on intent_log (ts desc) where not resolved;

-- новые категории не попадают в бюджет молча: сначала сюда, человек утверждает
create table if not exists category_candidate (
  id            bigserial primary key,
  name          text not null unique,
  proposed_text text,
  first_seen    timestamptz not null default now(),
  seen_count    integer not null default 1,
  approved      boolean,
  approved_by   bigint references participant(id),
  approved_at   timestamptz
);

-- очередь на доработку правил: фразы, где правила не справились
create or replace view v_intent_gaps as
select date_trunc('day', ts)::date as day,
       count(*) as всего,
       count(*) filter (where source = 'llm') as разобрала_модель,
       count(*) filter (where source in ('buttons','human_new')) as спросили_человека,
       count(*) filter (where not resolved) as не_записано
from intent_log
group by 1
order by 1 desc;

create or replace view v_top_unmatched as
select text, count(*) as раз, max(ts) as последний_раз
from intent_log
where source in ('buttons','human_new','failed')
group by text
order by раз desc, последний_раз desc
limit 100;

commit;

select count(*) as intent_log_rows from intent_log;
