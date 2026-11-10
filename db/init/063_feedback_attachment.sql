-- 063: скриншот в обращении «не работает» — связь «обращение ↔ файл» (Э6).
--
-- Зачем. Человек пишет «не работает» словами, потому что приложить нечего: в форме
-- `/webapp/feedback` поля файла нет, а личный файл админ не откроет (FR-K9). Дизайн и
-- решения D1–D8 — `docs/development/153`, спецификация — карты t_1ba005e2 и t_f00525ab.
--
-- Два изменения схемы:
--   * `file_object_scope_check` получает третье значение `'feedback'`: байты скриншота
--     живут в `file_object` (второго хранилища у проекта нет, `save_committed` —
--     единственное место, где появляется строка файла), но не в личном месте и не в
--     «Моих файлах»;
--   * таблица связи `feedback_attachment` — какое обращение какие файлы несёт.
--
-- Почему таблица, а не массив `feedback.attachment_ids`: массив не даёт ни FK, ни
-- уникальности, ни каскада, а «приложен ли этот файл к обращению» превращается в поиск
-- по массиву в каждом запросе доступа (D2). `unique (file_id)` держит правило «один файл
-- — одно обращение»: повторная отправка формы не задваивает вложение.
--
-- Номер. Максимум в `main` и во всех ветках на 10.11.2026 — 062
-- (`062_files_studio_access.sql`), 059 остаётся задокументированным пропуском
-- (`scripts/arch_drift.py`, `DOCUMENTED_GAPS[59]`). Беру 063: следующий свободный.
--
-- Прод. Миграция идемпотентна: повторный выкат ничего не меняет. Применяет владелец
-- отдельной картой деплоя, эта карта на прод не ходит.

begin;

alter table file_object drop constraint if exists file_object_scope_check;
alter table file_object add constraint file_object_scope_check
  check (scope in ('personal', 'shared', 'feedback'));

create table if not exists feedback_attachment (
    feedback_id bigint not null references feedback(id) on delete cascade,
    file_id     bigint not null unique references file_object(id) on delete cascade,
    position    int    not null default 0,
    created_at  timestamptz not null default now(),
    primary key (feedback_id, file_id)
);

create index if not exists ix_feedback_attachment_feedback
    on feedback_attachment (feedback_id, position);

commit;
