-- 012: идемпотентность записи в бюджет
-- Один и тот же источник (сообщение/черновик) не может создать две операции.
alter table budget_tx add column if not exists idempotency_key text;

-- обычный уникальный индекс, не частичный: ON CONFLICT (idempotency_key)
-- не умеет выводить частичные индексы. NULL в Postgres не конфликтует сам с собой.
create unique index if not exists ux_tx_idempotency on budget_tx (idempotency_key);

-- заполняем ключи для уже существующих записей бота, чтобы они не задвоились при повторе
update budget_tx
   set idempotency_key = 'legacy:' || id
 where idempotency_key is null and origin = 'bot';

select count(*) as всего, count(idempotency_key) as с_ключом from budget_tx;
