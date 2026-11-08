-- Миграции поверх схемы, созданной ранними версиями tool-api.
-- Идемпотентно, можно применять повторно.

-- люди из файлов, ещё не зарегистрированные в боте
alter table participant alter column tg_chat_id drop not null;
alter table participant add column if not exists is_active boolean not null default true;
alter table participant add column if not exists phone text;
alter table participant add column if not exists birthday date;

-- уникальность Telegram-ключа сохраняется, null-ы не конфликтуют
create unique index if not exists ux_participant_chat on participant (tg_chat_id);
