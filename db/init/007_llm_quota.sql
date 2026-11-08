-- 007: квоты AI на участника и учёт расхода
-- Цель: ни один человек не может «сжечь» общий бюджет провайдера.

begin;

create table if not exists llm_policy (
  id                  bigserial primary key,
  scope               text not null check (scope in ('global','role','participant')),
  scope_key           text,                       -- 'org' | 'иванов' | null для global
  daily_requests      int,
  monthly_tokens      bigint,
  monthly_cost_rub    numeric(10,2),
  max_tokens_request  int,
  model_allowlist     text[] not null default '{}',
  hard_stop           boolean not null default true,
  updated_by          bigint references participant(id),
  updated_at          timestamptz not null default now(),
  unique (scope, scope_key)
);

create table if not exists llm_usage (
  id            bigserial primary key,
  participant_id bigint references participant(id),
  ts            timestamptz not null default now(),
  model         text not null,
  tokens_in     int not null default 0,
  tokens_out    int not null default 0,
  cost_rub      numeric(10,4) not null default 0,
  intent        text,
  request_id    text,
  provider      text not null default 'openrouter',
  status        text not null default 'ok' check (status in ('ok','error','stopped'))
);
create index if not exists ix_usage_participant on llm_usage (participant_id, ts desc);
create index if not exists ix_usage_day on llm_usage (ts);

-- дефолты: org больше, студийцы меньше
insert into llm_policy (scope, scope_key, daily_requests, monthly_tokens, monthly_cost_rub,
                        max_tokens_request, hard_stop) values
  ('global', null,        200, 3000000, 1500.00, 4000, true),
  ('role',   'admin',     500, 8000000, 4000.00, 8000, false),
  ('role',   'org',       300, 4000000, 2000.00, 6000, true),
  ('role',   'troupe',     80, 1200000,  400.00, 2000, true),
  ('role',   'student',    60,  800000,  250.00, 2000, true)
on conflict (scope, scope_key) do nothing;

-- остаток на сегодня/месяц по каждому человеку
create or replace view v_llm_balance as
with used as (
  select p.id participant_id, p.display_name, p.role,
         count(*) filter (where u.ts::date = current_date) today_requests,
         coalesce(sum(u.tokens_in + u.tokens_out), 0) month_tokens,
         coalesce(sum(u.cost_rub), 0) month_cost_rub
  from participant p left join llm_usage u
       on u.participant_id = p.id and date_trunc('month', u.ts) = date_trunc('month', now())
  group by p.id, p.display_name, p.role
)
select u.*,
       coalesce(pol.daily_requests, rpol.daily_requests, gpol.daily_requests) daily_limit,
       coalesce(pol.monthly_tokens, rpol.monthly_tokens, gpol.monthly_tokens)   tokens_limit,
       coalesce(pol.monthly_cost_rub, rpol.monthly_cost_rub, gpol.monthly_cost_rub) cost_limit,
       coalesce(pol.hard_stop, rpol.hard_stop, gpol.hard_stop, true)            hard_stop,
       coalesce(pol.monthly_tokens, rpol.monthly_tokens, gpol.monthly_tokens, 0) - u.month_tokens tokens_left,
       coalesce(pol.daily_requests, rpol.daily_requests, gpol.daily_requests, 0) - u.today_requests requests_left
from used u
left join llm_policy pol on pol.scope='participant' and pol.scope_key = lower(u.display_name)
left join llm_policy rpol on rpol.scope='role' and rpol.scope_key = u.role
left join llm_policy gpol on gpol.scope='global' and gpol.scope_key is null;

commit;
