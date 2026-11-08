-- 033: признак «лист подтвердил запись» у операции бюджета (C-3)
--
-- Проблема: запись в лист «Транзакции» — best effort. Если Google ответил ошибкой,
-- операция остаётся в БД, но строки в листе нет. Сверка «файл → БД» видела такую
-- строку как «нет в листе» и помечала её rejected, хотя человек её не удалял.
--
-- Решение: две колонки состояния. sheet_written_at заполняется только при
-- успешном append, sheet_error — при сбое (текст ошибки, до 200 символов).
-- Сверка различает три случая: строка есть в листе (подтверждена),
-- строки нет и запись падала (не подтверждена — не отклонять),
-- строки нет и запись проходила (человек удалил — rejected).

begin;

alter table budget_tx add column if not exists sheet_written_at timestamptz;
alter table budget_tx add column if not exists sheet_error text;

comment on column budget_tx.sheet_written_at is
    'когда строка успешно добавлена в лист «Транзакции»; null — не подтверждено';
comment on column budget_tx.sheet_error is
    'ошибка записи в лист при последней попытке; null — ошибки не было';

commit;
