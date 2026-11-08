-- 009: поле подтверждения операции
alter table budget_tx add column if not exists confirmed_at timestamptz;
create index if not exists ix_tx_confirmed on budget_tx (confirmed_at desc);
