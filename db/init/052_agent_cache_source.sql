-- 052: журнал разбора знает ответ из кэша (карта A-4, docs/development/108 §10)
--
-- Зачем. Кэш повторяющихся вопросов («какое сегодня число», «кто сегодня занят»)
-- отвечает без вызова модели. Если такую сессию писать источником `rules`, экран
-- разбора и отчёты покажут «ответили правила» там, где сработал кэш, и доля кэша
-- в стоимости сообщения станет невидимой. Поэтому у источника появляется третье
-- осмысленное значение — `cache`:
--   * `llm`   — разобрала модель;
--   * `rules` — ответ по правилам (деградация или детерминированный ответ);
--   * `cache` — повтор вопроса в пределах TTL, модели не было, токенов не потрачено.
--
-- Номер 052: 050 занят в ветке feat/t_74ab8e2f-boot-beacon, 051 — в main
-- (`051_feedback_answer_server.sql`). Максимум по всем веткам +1.
--
-- Бюджет чата (`agently/budget.py`) считает только строки с `agent_status`,
-- и попадание в кэш идёт с `step_count = 0` и `tokens = 0` — суточный лимит
-- оно не расходует.
--
-- Повторный накат безопасен: drop constraint if exists + add.

begin;

alter table intent_log drop constraint if exists intent_log_source_check;

alter table intent_log
  add constraint intent_log_source_check
  check (source in ('rules', 'llm', 'buttons', 'human_new', 'failed', 'cache'));

comment on column intent_log.source is
  'Источник разбора: rules, llm, buttons, human_new, failed, cache (A-4)';

commit;
