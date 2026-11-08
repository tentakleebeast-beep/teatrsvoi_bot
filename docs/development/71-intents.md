# 71. Реестр намерений

Зачем делается работа и чем она закончилась. Одна строка — одна заметная работа
(сессия, фича, исправление группы дефектов). Ведёт агент сам.

Это не реестр требований (`docs/development/19-functional-requirements.md`) и не реестр
дефектов: намерение **ссылается** на их ID в поле «Основание» и не дублирует статус.

Связанные реестры: уроки — `docs/development/69-lessons.md`,
препятствия — `docs/development/70-obstacles.md`. Проверка — `scripts/check_registers.py`.

---

## 1. Правила ведения

1. **Намерение заводится до начала работы**, а не задним числом.
2. **Основание** — ID требований и дефектов (`FR-D18`, `S-8`, `C-4`) или запрос
   с датой («запрос Виктора 28.10.2026»). Работа без основания не начинается.
3. **Коммиты ссылаются на намерение**: `fix(api): ... (INT-002)`.
4. **«Выполнено» — только с артефактами**: файлы или коммиты в обратных кавычках;
   проверка убеждается, что они существуют.
5. Если работа упёрлась в человека — статус «отложено» и ссылка на O-NNN в «Артефактах».

| Статус | Что требует проверка |
|---|---|
| активно | — |
| выполнено | есть хотя бы один существующий файл или коммит |
| отложено | — |
| отменено | — |

---

## 2. Реестр

| ID | Дата | Намерение | Основание | Статус | Артефакты |
|---|---|---|---|---|---|
| INT-001 | 28.10.2026 | Ввести три реестра процесса (уроки, препятствия, намерения), которые агент ведёт сам, и проверку к ним | запрос Виктора 28.10.2026 (разбор OpenBot: EVOLUTION/LESSONS, DOLEANCES, INTENT); `docs/reports/68-state-review.md` раздел 5 | выполнено | `docs/development/69-lessons.md`, `docs/development/70-obstacles.md`, `docs/development/71-intents.md`, `scripts/check_registers.py`, `tests/unit/test_registers.py` |
| INT-002 | 28.10.2026 | Запустить сервисы бота и закрыть остаточные дефекты smoke-тестов | запрос Виктора 28.10.2026; S-8, B-14, C-3, C-8, C-13 — `docs/reports/68-state-review.md` раздел 3 | выполнено | `scripts/start_services.py`, `tests/integration/test_smoke_api_fixes.py`, коммит 90be2d9 (в приватной истории, docs/development/26 §10) |
| INT-003 | 28.10.2026 | Блокеры релиза: отмена и перенос события, второе подтверждение крупной суммы, автоначисление взносов, планировщик в контуре, супервизор, webhook-режим | запрос Виктора 28.10.2026; FR-D18, FR-D19, FR-B5, FR-B7, C-11, FR-H12, FR-H13, R-3, R-5 — `docs/reports/63-release-readiness.md` | выполнено | `tests/integration/test_event_cancel.py`, `tests/integration/test_event_move.py`, `db/init/034_dues_tariff.sql`, `deploy/systemd`, `scripts/health_agent.py`, коммит f7e0d62 (в приватной истории, docs/development/26 §10); режим webhook отложен после релиза (Ш-12); второе подтверждение крупной суммы снято 30.10.2026 — порога нет (`docs/architecture/25-architecture-evolution.md` §4) |
| INT-004 | 28.10.2026 | Разобрать обращения из бота: закрыть исправленное, починить баги и сделать доработки | запрос Виктора 28.10.2026; обращения №8–№17 в `feedback`, разбор в `docs/reports/72-work-plan.md` | выполнено | `tests/integration/test_feedback.py`, `tests/integration/test_feedback_digest.py`, `docs/reports/77-github-references.md`, коммит 90be2d9 (в приватной истории, docs/development/26 §10) |
| INT-005 | 28.10.2026 | Переезд бота на удалённый сервер: инструкция «Найти домик для Бота Театра», скрипты развёртывания с переменными адресами, план переключения поллера | запрос Виктора 28.10.2026; `docs/infrastructure/24`, `docs/infrastructure/35`, `docs/reports/63-release-readiness.md` | активно | `docs/infrastructure/75-hosting-and-migration.md`, `deploy/server-deploy.sh`, `deploy/poller-switch.sh`, `tests/unit/test_deploy_scripts.py` |
| INT-006 | 30.10.2026 | Разбор обратной связи отдельным заданием каждые 10 минут: журнал ответов в БД, автоматические ответы автору, нерешаемое — вопросом в файл 74 | запрос Виктора 30.10.2026; V-024, пересмотр V-012; docs/reports/72 раздел «Как исполнять» | выполнено | `db/init/047_feedback_answers.sql`, `scripts/feedback_queue.py`, `tests/unit/test_feedback_queue.py`, `tests/integration/test_feedback.py` |
| INT-007 | 01.11.2026 | Вывести n8n из контура целиком: роутинг и LLM в tool-api (`POST /tools/route`), публичный вебхук роутера закрыт, публичный адрес и переменные `N8N_*` убраны из деплоя | запрос Виктора 31.10.2026; решение V-026 «убрать целиком» 01.11.2026; `docs/archive/88-n8n-security-and-efficiency.md` | выполнено | `services/tool-api/app/router_llm.py`, `services/tool-api/app/api/route.py`, `tests/unit/test_router_llm.py`, `tests/integration/test_tools_route.py`, `scripts/check_router_scenarios.py`, `deploy/templates/Caddyfile.template` |
| INT-008 | 01.11.2026 | Не больше одной карты на область в работе: область выводится из тела карты, проверка в доске, сторож держит карты занятых областей | двойной конфликт webapp-веток с общей оболочкой 31.10.2026; правило владельца «одна карта на область»; карта t_063f2d88 | активно | `scripts/kanban_areas.py`, `scripts/check_kanban_cards.py`, `tests/unit/test_kanban_areas.py`, `docs/development/26-process-and-git.md` |
| INT-009 | 01.11.2026 | Агентный режим свободного текста: `POST /tools/agent/ask` с закрытым реестром инструментов только для чтения, предложением записи вместо записи и деградацией к правилам | запрос Виктора 01.11.2026 после дефекта «бот ответил датой 2023 года»; `docs/development/108` | активно | `services/tool-api/app/agently/`, `services/tool-api/app/api/agent_ask.py`, `services/tg-gateway/app/core.py`, `tests/integration/test_agent_ask.py`, `tests/fixtures/agent_questions.json`, `scripts/check_agent_questions.py`, `docs/development/108-agent-mode.md` |
| INT-010 | 01.11.2026 | Наблюдаемость агента: шаги, инструменты и обезличенные аргументы в `intent_log`, экран разбора в админке, ретенция как у журнала интентов | карта A-5 `t_1475ac62` плана агентного режима 01.11.2026 — без этого «чепуху» нельзя разобрать по фактам; `docs/development/108` §6 | выполнено | `db/init/048_agent_observability.sql`, `services/tool-api/app/masking.py`, `services/tool-api/app/api/agent_ask.py`, `services/tool-api/app/api/admin.py`, `services/tool-api/app/pages/admin_agent.py`, `tests/unit/test_agent_masking.py`, `tests/integration/test_agent_observability.py` |
| INT-011 | 01.11.2026 | Ревизия документации: устаревшее — в архив с пометкой «Заменён», макеты — в `docs/archive/prototypes/`, действующие документы актуализированы по фактам кода, индекс и правило архива пересобраны | карта `t_13d4c50d`; запрос Виктора 31.10.2026 «навести порядок в папке docs: архивировать устаревшее и прототипы» | выполнено | `docs/archive/README.md`, `docs/archive/prototypes/`, `prototypes/README.md`, `scripts/build_docs_index.py`, `tests/unit/test_docs_links.py` |
| INT-012 | 01.11.2026 | Контракт API не меняется молча: снимок фактической `app.openapi()` в `contracts/openapi.json`, точечный дифф с путём и обоими значениями, обновление только явным `--write`, шаг проверки в job `tests` и правило в `docs/development/26` §4a | запрос Виктора 01.11.2026; переименования полей и путей при разрезе tool-api на роутеры ловились только тестами, которые кто-то не забыл обновить; карта `t_0ee3c210` | выполнено | `contracts/openapi.json`, `scripts/openapi_snapshot.py`, `tests/integration/test_openapi_contract.py`, `docs/development/26-process-and-git.md` |
| INT-013 | 02.11.2026 | «Сегодня» в проверке реестров считается в проектной зоне из настроек сервиса: job `tests` на `main` зелёный в вечернем окне UTC, зона процесса на вердикт не влияет | CI run 41361984335 на `main` e30abda: `tests` упал на `71-intents.md INT-012: дата в будущем`; карта `t_1ac583ea` плана 02.11.2026 | выполнено | `scripts/check_registers.py`, `tests/unit/test_registers.py`, `docs/development/69-lessons.md` |
| INT-014 | 01.11.2026 | Измерить агентный режим до его кода: набор из 30 реальных вопросов с классами и эталонными фактами, метрика ответов и цифры по моделям-кандидатам | запрос Виктора 01.11.2026 (агентный режим вместо одиночного вызова LLM); дефект «бот ответил датой 2023 года» — карта A-1; карта `t_ef344153` | выполнено | `scripts/agent_eval.py`, `scripts/agent_eval_set.json`, `tests/agent/test_agent_eval.py`, `tests/agent/test_agent_eval_set.py`, `docs/reports/112-agent-eval.md` |
| INT-015 | 02.11.2026 | Разделить роли «классификатор / исполнитель», поставить дневной бюджет на чат и кэш повторяющихся вопросов, посчитать стоимость 1000 сообщений по замеру A-0 | план агентного режима 01.11.2026; цифры карты A-0 (`docs/reports/112`); карта `t_340bc27b` | выполнено | `config/agent-models.yaml`, `services/tool-api/app/agently/models.py`, `services/tool-api/app/agently/budget.py`, `services/tool-api/app/agently/cache.py`, `scripts/agent_cost.py`, `docs/reports/113-agent-models-budget.md`, `docs/reports/113-agent-cost.json` |
| INT-016 | 02.11.2026 | Разобрать «домен недоступен из РФ без VPN»: повторяемый замер, мониторинг доступности и порядок действий при блокировке (карта `t_40ec6e15`) | карта `t_40ec6e15` от 02.11.2026; четыре часа диагностики 02.11.2026 без замера | выполнено | `scripts/check_availability.py`, `tests/unit/test_availability.py`, `docs/infrastructure/75-hosting-and-migration.md`, `deploy/systemd/svoi-geo-check.service`, `deploy/systemd/svoi-geo-check.timer` |
| INT-017 | 04.11.2026 | Оценить VK Mini App как второй фронт после решения владельца «домен один, проблема в доступности Telegram»: что переносится, какие разрывы, сколько работы, что нужно от владельца | дополнение владельца 02.11.2026 и решение 03.11.2026 в карте `t_40ec6e15`; замер доступности 02.11.2026 показал, что домен из РФ доступен | активно | `docs/reports/116-vk-miniapp-second-front.md`, `docs/infrastructure/75-hosting-and-migration.md`, `docs/development/70-obstacles.md` |
| INT-018 | 04.11.2026 | Разобрать 81 локальную ветку воркеров: отделить уже влитое от работы, которой нет в `main`, и влить остаток по одной ветке через ревью | запрос Виктора 04.11.2026 (ветки от воркеров скопились, непонятно, нужны ли) | выполнено | `docs/plans/123-leftover-branches.md`, `scripts/check_done.py`, `scripts/kanban_risk_route.py`, `scripts/check_settings.py`, коммиты c48cc03, d10b83e, e35645e, aec2de0, 396341f, acffc8d (в приватной истории, docs/development/26 §10) |
| INT-019 | 04.11.2026 | Закрыть сироту «заявка об оплате взноса» по решению владельца: функции нет, взносы только импортом; удалить маршрут, перевести строку Д7 реестра в «нет», закрепить инвариант I4 тестом | карта `t_28e08a7f` (AUD-12); решение владельца 04.11.2026 в комментарии к карте; `docs/reports/74-questions.md` V-016; `docs/architecture/25-architecture-evolution.md` §9.2 | выполнено | `services/tool-api/app/api/finance.py`, `tests/integration/test_dues_tariff.py`, `tests/webapp/server.py`, `contracts/openapi.json`, `docs/architecture/25-architecture-evolution.md`, `docs/reports/117-actions-inventory.md` |

---

*Добавить намерение:* следующий ID, сегодняшняя дата, статус «активно», затем
`env -u PYTHONPATH .venv/bin/python scripts/check_registers.py`.
