-- 055: причина деградации в журнале разбора и настоящая цена в учёте (карта AUD-10)
--
-- Зачем. docs/reports/119 §9 п.4–5 и §10 п.4–5: у трёх отказов 01.11 причина
-- (`degraded.reason`) лежала только в `audit_event.after`, а `llm_usage` писала
-- `tokens_in = 0`, `tokens_out = все токены`, `cost_rub = 0`. Поэтому «сколько отказов
-- из-за провайдера, сколько из-за бюджета» по журналу не считалось, а цифра стоимости
-- была замером из docs/reports/113, а не боевыми данными.
--
-- Что здесь:
--   1. `intent_log.degraded_reason` — причина отказа рядом с самим отказом.
--      Закрытого CHECK нет намеренно: список причин задаёт код (`agently/budget.py`,
--      `agently/loop.py`), и новая причина не должна ронять запись 500-й ошибкой ровно
--      в тот момент, когда провайдер уже недоступен. Вид причины (провайдер / бюджет)
--      считается представлением `v_intent_degraded`, а не хранится второй копией.
--   2. `llm_usage.tokens_in` / `tokens_out` — теперь разные числа (раньше вход был 0,
--      а в выход писались все токены).
--   3. `llm_usage.cost_rub` — рубли по ценам `config/agent-models.yaml` и курсу оттуда же.
--      NOT NULL и DEFAULT 0 сняты: ноль означал бы «посчитано и вышло даром», а нам
--      нужно «цены нет» — это NULL плюс отдельное поле `price_status = 'no_price'`.
--
-- Номер. Максимум по всем ветвям и worktree — 054 (`folder_roots`), номера 055 свободен.
-- Пропуск 050 (между 049 и 051) — не потерянный файл: `050_*.sql` не было ни в одной
-- ветви и ни в одной ревизии истории (проверено `git ls-tree` по `refs/heads` и
-- `refs/remotes/origin` и `git log --all -- db/init/050*`). Занимать пропуск задним
-- числом нельзя: порядок применения — сортировка по имени файла, и 050 встал бы в
-- середину уже применённой на проде цепочки. Так же решались 053 и 054
-- (docs/reports/115).
--
-- Прод. Миграция написана идемпотентной (`if not exists`, `drop not null` повторно
-- безопасен) и на проде её применяет владелец отдельной картой деплоя
-- (docs/reports/119 §11 п.3), а не эта карта.

begin;

-- ---------------------------------------------------------------- причина отказа
alter table intent_log
  add column if not exists degraded_reason text;

comment on column intent_log.degraded_reason is
  'Причина деградации агентной сессии (AUD-10): chat_tokens, chat_steps, token_budget, '
  'timeout, llm_unavailable, llm_error, provider_timeout, step_limit. NULL — отказа не было '
  'или строка старше миграции 055';

-- Экран разбора и отчёты читают отказы, а не весь журнал: частичный индекс не растёт
-- вместе с удачными сессиями.
create index if not exists ix_intent_log_degraded on intent_log (ts desc)
  where degraded_reason is not null;

-- ------------------------------------------- вид причины: одним запросом, без второй копии
-- Соответствие причин и видов живёт здесь одно: код пишет причину, представление её
-- объясняет. Один select по представлению отвечает на вопрос разбора:
--     select вид, sum(отказов) from v_intent_degraded group by вид order by 2 desc;
create or replace view v_intent_degraded as
select date_trunc('day', ts)::date as день,
       coalesce(degraded_reason, 'не записано') as причина,
       case
         when degraded_reason in ('chat_tokens', 'chat_steps', 'token_budget') then 'бюджет'
         when degraded_reason in ('timeout', 'llm_unavailable', 'llm_error',
                                  'provider_timeout') then 'провайдер'
         when degraded_reason = 'step_limit' then 'агент'
         when degraded_reason is null then 'не записано'
         else 'прочее'
       end as вид,
       count(*) as отказов
from intent_log
where agent_status = 'refusal'
group by 1, 2, 3;

comment on view v_intent_degraded is
  'Отказы агента по дням и причинам (AUD-10): «сколько из-за провайдера, сколько из-за бюджета»';

-- ---------------------------------------------------------------- настоящая цена
alter table llm_usage alter column cost_rub drop not null;
alter table llm_usage alter column cost_rub drop default;

alter table llm_usage
  add column if not exists price_status text not null default 'no_price';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'llm_usage_price_status_chk') then
    alter table llm_usage
      add constraint llm_usage_price_status_chk
      check (price_status in ('known', 'no_price'));
  end if;
end $$;

comment on column llm_usage.cost_rub is
  'Стоимость запроса в рублях по ценам config/agent-models.yaml и курсу оттуда же (AUD-10). '
  'NULL при price_status = no_price: цены модели нет — это не ноль. Строки до миграции 055 '
  'могли содержать 0, не посчитав цену';
comment on column llm_usage.price_status is
  'Цена известна (known) или нет (no_price). Заменяет ноль в cost_rub там, где считать было нечем';

commit;

-- Проверка после применения: причины отказов и признак цены видны.
select agent_status,
       coalesce(degraded_reason, '—') as причина,
       count(*) as строк
  from intent_log
 where agent_status = 'refusal'
 group by 1, 2
 order by 3 desc;
