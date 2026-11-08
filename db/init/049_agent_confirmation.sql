-- 049: одноразовые токены подтверждения предложений агента (A-3, docs/development/108 §8).
--
-- Номер 049, а не 048: 048 занят картой A-5 (`t_1475ac62`, `048_agent_observability.sql`)
-- в отдельной ветке. Пропуск номера безопаснее дубля — два файла с одним номером
-- в этом проекте уже были (047).
--
-- Зачем таблица, а не состояние в боте: подтверждение — это право записать деньги.
-- Оно обязано проверяться на сервере, переживать перезапуск бота и не зависеть от
-- того, помнит ли FSM, что человек нажимал кнопку. Токен живёт 5 минут и срабатывает
-- один раз: повторное нажатие не создаёт вторую операцию.
begin;

create table if not exists agent_proposal (
  id             bigserial primary key,
  token          text not null unique,
  chat_id        bigint not null,
  participant_id bigint not null references participant(id) on delete cascade,
  tool           text not null,
  args           jsonb not null default '{}'::jsonb,
  human_text     text not null default '',
  status         text not null default 'pending'
                   check (status in ('pending','confirmed','cancelled','expired','failed')),
  result         jsonb,
  error          text,
  created_at     timestamptz not null default now(),
  expires_at     timestamptz not null,
  resolved_at    timestamptz,
  confirmed_by   bigint references participant(id)
);

comment on table agent_proposal is
  'Предложение агента: агент предлагает действие, исполняет детерминированный код по кнопке';
comment on column agent_proposal.token is
  'Одноразовый токен подтверждения: 5 минут, привязан к чату, человеку и аргументам';
comment on column agent_proposal.args is
  'Аргументы, уже проверенные инструментом предложения (категория, сумма, дата)';

-- Очередь «ждёт подтверждения» по чату: единственный рабочий срез таблицы.
create index if not exists ix_agent_proposal_pending on agent_proposal (chat_id, status)
  where status = 'pending';

commit;
