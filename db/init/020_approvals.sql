-- 020: подтверждение крупных операций администратором (FR-B5)
-- Решение заказчика 26.10.2026: траты и доходы выше порога подтверждает админ,
-- уведомление приходит ему в чат. Порог — config `confirm_above_amount` (100 000 ₽).

begin;

create table if not exists approval_request (
  id             bigserial primary key,
  payload        jsonb not null,              -- тело исходной операции
  amount         numeric(12,2) not null,
  tx_type        text not null,
  requested_by   bigint references participant(id),
  chat_id        bigint,
  status         text not null default 'pending'
                 check (status in ('pending', 'approved', 'rejected', 'expired')),
  created_at     timestamptz not null default now(),
  decided_by     bigint references participant(id),
  decided_at     timestamptz,
  comment        text,
  budget_tx_id   bigint references budget_tx(id),
  idempotency_key text
);

create unique index if not exists ux_approval_key on approval_request (idempotency_key);
create index if not exists ix_approval_pending on approval_request (created_at desc)
  where status = 'pending';

-- возможность только для администратора
insert into rbac_capability (code, title, grp, description)
values ('budget.approve', 'Подтверждение крупных операций', 'budget',
        'Подтверждает или отклоняет траты и доходы выше порога')
on conflict (code) do nothing;

insert into rbac_role_capability (role, capability)
select 'admin', 'budget.approve'
on conflict do nothing;

commit;

select r.code, r.title from rbac_capability r where r.code = 'budget.approve';
select role, count(*) from rbac_role_capability group by 1 order by 1;
