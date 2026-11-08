# Документация проекта

Действующие документы сгруппированы по смыслу. **Нумерация сквозная**: номер означает
порядок появления документа и никогда не переиспользуется — по нему ссылаются код,
тесты и другие документы. Номера не использованы: 39, 119, 133, 134.

Устаревшее не удаляется, а лежит в [архиве](archive/) — отдельной строкой в конце
индекса. Правило, что туда попадает и как на него ссылаться: `docs/archive/README.md`.

| Группа | Что внутри | Документов |
|---|---|---|
| [Архитектура](architecture/) | модель данных, правила, алгоритмы, границы систем | 21 |
| [Инфраструктура](infrastructure/) | Google, календари, уведомления, развёртывание | 10 |
| [Разработка](development/) | требования, сценарии, тесты, процессы, интерфейсы | 35 |
| [Планы](plans/) | роадмап, планы развития | 8 |
| [Отчёты](reports/) | датированные срезы: чекпойнты, аудиты, покрытие, смоук-тесты | 55 |
| [Архив](archive/) | устаревшее и историческое; не правило, а запись | 7 |

## Архитектура

*модель данных, правила, алгоритмы, границы систем*

| № | Документ | О чём |
|---|---|---|
| 01 | [01-data-model.md](architecture/01-data-model.md) | 01. Модель данных |
| 02 | [02-access-control.md](architecture/02-access-control.md) | 02. Роли, матрица доступа, регистрация |
| 03 | [03-budget.md](architecture/03-budget.md) | 03. Бюджет: регламент разбора и работа с файлом |
| 04 | [04-schedule.md](architecture/04-schedule.md) | 04. Занятость, сводное расписание, окна, календарь |
| 05 | [05-notifications-and-audit.md](architecture/05-notifications-and-audit.md) | 05. Уведомления, аудит, защита от галлюцинаций |
| 10 | [10-data-model-v2.md](architecture/10-data-model-v2.md) | 10. Модель данных v2: что нашли в файлах и как учёт устроен теперь |
| 11 | [11-business-rules.md](architecture/11-business-rules.md) | 11. Бизнес-правила и инварианты логики |
| 17 | [17-business-algorithms.md](architecture/17-business-algorithms.md) | 17. Бизнес-алгоритмы: как человек передаёт данные и как бот их понимает |
| 25 | [25-architecture-evolution.md](architecture/25-architecture-evolution.md) | 25. Архитектурные идеи: внедрить, развить, упразднить |
| 30 | [30-architecture-c4.md](architecture/30-architecture-c4.md) | 30. Архитектура в C4-подобной нотации |
| 40 | [40-slot-search.md](architecture/40-slot-search.md) | 40. Поиск окон: критерии, две области, входы |
| 41 | [41-events-cast-and-subjects.md](architecture/41-events-cast-and-subjects.md) | 41. События, роли и разбор субъекта запроса |
| 42 | [42-window-policies.md](architecture/42-window-policies.md) | 42. Строгость окон: аренда, показ, репетиция, тренинг |
| 45 | [45-calendar-events-as-busy.md](architecture/45-calendar-events-as-busy.md) | 45. События календаря как занятость |
| 46 | [46-venue-availability.md](architecture/46-venue-availability.md) | 46. Занятость зала и аренда |
| 47 | [47-venues.md](architecture/47-venues.md) | 47. Пространства: сцена, гримёрка, подсобки |
| 50 | [50-event-assignment.md](architecture/50-event-assignment.md) | 50. Назначение события: шаг, которого не было |
| 51 | [51-assignment-implementation.md](architecture/51-assignment-implementation.md) | 51. Назначение события: что сделано |
| 52 | [52-twins-teachers-director.md](architecture/52-twins-teachers-director.md) | 52. Тёзки, педагоги и режиссёр |
| 85 | [85-error-catalog.md](architecture/85-error-catalog.md) | 85. Каталог ошибок HTTP API |
| 140 | [140-master-role.md](architecture/140-master-role.md) | 140. Роль «Педагог» (master): модель, дисциплины, права |

## Инфраструктура

*Google, календари, уведомления, развёртывание*

| № | Документ | О чём |
|---|---|---|
| 08 | [08-infrastructure.md](infrastructure/08-infrastructure.md) | 08. Инфраструктура: локальная разработка и целевой контур |
| 09 | [09-google-setup.md](infrastructure/09-google-setup.md) | 09. Google: service account и структура Drive с нуля |
| 24 | [24-infrastructure-deploy.md](infrastructure/24-infrastructure-deploy.md) | 24. Инфраструктура и переезд в прод |
| 35 | [35-deploy-runbook.md](infrastructure/35-deploy-runbook.md) | 35. Runbook развёртывания на удалённом сервере |
| 36 | [36-notifications.md](infrastructure/36-notifications.md) | 36. Уведомления и напоминания |
| 44 | [44-google-calendar-capabilities.md](infrastructure/44-google-calendar-capabilities.md) | 44. Что даёт Google Calendar, а что придётся держать у себя |
| 75 | [75-hosting-and-migration.md](infrastructure/75-hosting-and-migration.md) | 75. Найти домик для Бота Театра: от покупки сервера до прода |
| 110 | [110-backups-and-restore.md](infrastructure/110-backups-and-restore.md) | 110. Бэкапы БД: где лежат, сколько живут, как восстановить |
| 118 | [118-vk-chat-bot-or-screens.md](infrastructure/118-vk-chat-bot-or-screens.md) | 118. VK: аналог бота, чат или экраны — что говорит документация |
| 139 | [139-release-checklist-v0.7.0.md](infrastructure/139-release-checklist-v0.7.0.md) | 139. Чек-лист выката v0.7.0 |

## Разработка

*требования, сценарии, тесты, процессы, интерфейсы*

| № | Документ | О чём |
|---|---|---|
| 12 | [12-bot-behavior.md](development/12-bot-behavior.md) | 12. Поведение бота: манифесты, формы, квоты, границы настройки |
| 13 | [13-registration.md](development/13-registration.md) | 13. Регистрация: admin, организатор, студиец, педагог |
| 14 | [14-files-and-routing.md](development/14-files-and-routing.md) | 14. Файлы, БД и маршрутизация запросов |
| 16 | [16-test-strategy.md](development/16-test-strategy.md) | 16. Требования к тестированию и стратегия покрытия |
| 19 | [19-functional-requirements.md](development/19-functional-requirements.md) | 19. Функциональные требования |
| 20 | [20-scenarios.md](development/20-scenarios.md) | 20. Пользовательские сценарии |
| 21 | [21-intent-cascade.md](development/21-intent-cascade.md) | 21. Каскад разбора: правила → модель → человек |
| 22 | [22-schedule-fill-rules.md](development/22-schedule-fill-rules.md) | 22. Заполнение расписания цветом: правила и разбор |
| 23 | [23-ux-scenarios.md](development/23-ux-scenarios.md) | 23. Сценарии взаимодействия и пользовательский опыт |
| 26 | [26-process-and-git.md](development/26-process-and-git.md) | 26. Процесс работы с кодом и версии |
| 28 | [28-use-cases.md](development/28-use-cases.md) | 28. Варианты использования (Use Cases) и покрытие тестами |
| 31 | [31-user-guide.md](development/31-user-guide.md) | 31. Инструкция для пользователей |
| 34 | [34-code-audit.md](development/34-code-audit.md) | 34. Аудит кода |
| 43 | [43-registration-questionnaire.md](development/43-registration-questionnaire.md) | 43. Анкета участника в Mini App |
| 48 | [48-mini-app-grid.md](development/48-mini-app-grid.md) | 48. Лист занятости участника в Mini App (M1) |
| 53 | [53-disciplines-and-search-form.md](development/53-disciplines-and-search-form.md) | 53. Дисциплины и форма поиска окон |
| 55 | [55-requirements-review.md](development/55-requirements-review.md) | 55. Ревью требований: что добавлено и что нужно решить |
| 56 | [56-code-review.md](development/56-code-review.md) | 56. Ревью кода и тестов, 27.10.2026 |
| 57 | [57-channels-and-routing.md](development/57-channels-and-routing.md) | 57. Каналы доставки информации |
| 58 | [58-regression-test-plan.md](development/58-regression-test-plan.md) | 58. Тест-план регрессионного прогона |
| 69 | [69-lessons.md](development/69-lessons.md) | 69. Уроки: ошибка → правило → где закреплено |
| 70 | [70-obstacles.md](development/70-obstacles.md) | 70. Препятствия: где агенту нужен человек |
| 71 | [71-intents.md](development/71-intents.md) | 71. Реестр намерений |
| 73 | [73-admin-panel-webapp.md](development/73-admin-panel-webapp.md) | 73. Админ-панель в Mini App |
| 81 | [81-webapp-browser-tests.md](development/81-webapp-browser-tests.md) | 81. Браузерные тесты Mini App: Playwright, тема и токен |
| 82 | [82-ux-navigation.md](development/82-ux-navigation.md) | 82. Интерфейс бота: исследование, аудит и новая навигация |
| 86 | [86-files-and-storage.md](development/86-files-and-storage.md) | 80. Файлы бота: хранилище, папки, аудио и агентные операции — требования |
| 87 | [87-agent-files.md](development/87-agent-files.md) | 81. Агент и файлы: где живёт цикл «модель → инструмент → модель» |
| 90 | [90-ui-review.md](development/90-ui-review.md) | 84. Разбор интерфейса Mini App: критика, противоречия, упущения |
| 91 | [91-design-system.md](development/91-design-system.md) | 85. Дизайн-система Mini App «СВОИ», версия 2 |
| 92 | [92-navigation-model.md](development/92-navigation-model.md) | 86. Модель навигации Mini App |
| 93 | [93-cjm.md](development/93-cjm.md) | 87. CJM: пути людей в Mini App |
| 107 | [107-kanban-risk-routing.md](development/107-kanban-risk-routing.md) | 107. Проверка по риску: когда карту смотрит worker-test |
| 108 | [108-agent-mode.md](development/108-agent-mode.md) | 108. Агентный режим свободного текста: `/tools/agent/ask` |
| 125 | [125-kanban-backoff.md](development/125-kanban-backoff.md) | 125. Отступление после лимита провайдера и потолок прогонов |

## Планы

*роадмап, планы развития*

| № | Документ | О чём |
|---|---|---|
| 07 | [07-roadmap-metrics.md](plans/07-roadmap-metrics.md) | 07. Дорожная карта, метрики успеха, риски |
| 37 | [37-mini-app-plan.md](plans/37-mini-app-plan.md) | 37. Mini App: план реализации |
| 89 | [89-autonomous-development.md](plans/89-autonomous-development.md) | 89. Автономная разработка: канбан-поток вместо ручных заходов |
| 94 | [94-ux-ui-rework-plan.md](plans/94-ux-ui-rework-plan.md) | 88. План переработки UI/UX Mini App |
| 123 | [123-leftover-branches.md](plans/123-leftover-branches.md) | 123. Остатки веток воркеров: что влить, что закрыть |
| 126 | [126-code-refactor-plan.md](plans/126-code-refactor-plan.md) | 126. План рефакторинга кода и тестов под целевую модель домов |
| 128 | [128-scope-now-and-roadmap.md](plans/128-scope-now-and-roadmap.md) | 128. Границы прохода: что рефакторим сейчас и что уходит в дорожную карту |
| 129 | [129-public-repo-plan.md](plans/129-public-repo-plan.md) | 129. Публичный репозиторий: что вычистить, чем проверить, в каком порядке |

## Отчёты

*датированные срезы: чекпойнты, аудиты, покрытие, смоук-тесты*

| № | Документ | О чём |
|---|---|---|
| 27 | [27-audit-and-conclusions.md](reports/27-audit-and-conclusions.md) | 27. Аудит документации и главные выводы |
| 32 | [32-requirements-audit.md](reports/32-requirements-audit.md) | 32. Аудит требований: что было завышено |
| 33 | [33-test-coverage.md](reports/33-test-coverage.md) | 33. Покрытие тестами и матрица use cases |
| 49 | [49-checkpoint-2026-10-27.md](reports/49-checkpoint-2026-10-27.md) | 49. Чекпоинт проекта, 27.10.2026 |
| 54 | [54-smoke-report.md](reports/54-smoke-report.md) | 54. Смоук-тест всех функций, 27.10.2026 |
| 59 | [59-smoke-2-report.md](reports/59-smoke-2-report.md) | 59. Второй смоук: обратная связь, профиль, каналы |
| 60 | [60-finance-sheet-sync.md](reports/60-finance-sheet-sync.md) | 60. Перенос операций в человеческий лист: состояние и вывод |
| 61 | [61-session-handover.md](reports/61-session-handover.md) | 61. Хвосты сессии 27.10.2026 |
| 62 | [62-smoke-api.md](reports/62-smoke-api.md) | 62. Smoke-тест HTTP API `tool-api` |
| 63 | [63-release-readiness.md](reports/63-release-readiness.md) | 63. Готовность к раскатке на публичную инфраструктуру |
| 64 | [64-critics-and-debts.md](reports/64-critics-and-debts.md) | 64. Критики, планировщик и долги проекта |
| 65 | [65-smoke-buttons.md](reports/65-smoke-buttons.md) | 65. Smoke: кнопки и сценарии бота |
| 66 | [66-smoke-webapp.md](reports/66-smoke-webapp.md) | 66. Smoke: Mini App |
| 67 | [67-intent-routing.md](reports/67-intent-routing.md) | 67. Разбор намерения: почему бюджет перехватывал назначение и что с этим делать |
| 68 | [68-state-review.md](reports/68-state-review.md) | 68. Состояние проекта на 28.10.2026: план, открытые дефекты, процесс |
| 72 | [72-work-plan.md](reports/72-work-plan.md) | 72. План работ: остаточные дефекты smoke и блокеры релиза |
| 74 | [74-questions.md](reports/74-questions.md) | 74. Вопросы к Виктору |
| 76 | [76-test-audit.md](reports/76-test-audit.md) | 76. Аудит тестов: слияние вариантов и отчёт (Ш-51.2) |
| 77 | [77-github-references.md](reports/77-github-references.md) | 77. 10 референсов с GitHub: Telegram-боты для небольших сообществ |
| 78 | [78-release-state.md](reports/78-release-state.md) | 78. Состояние релиза |
| 79 | [79-code-architecture-review.md](reports/79-code-architecture-review.md) | 79. Ревью архитектуры кода (V-014, обращение №33) |
| 80 | [80-manual-testing-night.md](reports/80-manual-testing-night.md) | 80. Мануальное тестирование бота в Telegram: ночной прогон |
| 84 | [84-api-errors-and-dead-code.md](reports/84-api-errors-and-dead-code.md) | 84. Ревью ошибок API и мёртвый код |
| 95 | [95-ux-measurements.md](reports/95-ux-measurements.md) | 89. Измерения интерфейса Mini App |
| 96 | [96-kanban-audit-31.10.2026.md](reports/96-kanban-audit-31.10.2026.md) | 96. Аудит канбан-доски и состояния проекта |
| 97 | [97-tool-api-routers.md](reports/97-tool-api-routers.md) | 97. tool-api: роутеры по домену, main.py — фабрика приложения (R-1) |
| 98 | [98-tg-gateway-routers.md](reports/98-tg-gateway-routers.md) | 98. Гейтвей: роутеры по домену и texts.py (R-2) |
| 99 | [99-shared-helpers.md](reports/99-shared-helpers.md) | 99. Общие помощники: format.py, scripts/_cli.py, JS api()/auth() (R-6, R-8) |
| 100 | [100-narrow-except-and-types.md](reports/100-narrow-except-and-types.md) | 100. Узкие `except` вместо широких и mypy на чистых модулях (R-5, R-7) |
| 101 | [101-webapp-week-money-split.md](reports/101-webapp-week-money-split.md) | 101. Webapp: `week.py` и `money.py` — остаток порога 400 строк (R-9) |
| 102 | [102-webapp-merge-with-shell.md](reports/102-webapp-merge-with-shell.md) | 102. Webapp: переделка страниц сведена с общей оболочкой D (W-1) |
| 103 | [103-gateway-green-gate.md](reports/103-gateway-green-gate.md) | 100. Гейтвей: зелёный гейт после выноса роутеров (C-1) |
| 104 | [104-test-audit.md](reports/104-test-audit.md) | 104. Аудит тестов после рефакторинга: 1762 случая, которые не ловят баги |
| 105 | [105-test-audit-gate.md](reports/105-test-audit-gate.md) | 105. Проверка обоснованности тестов: шаг в CI `lint` и в pre-commit |
| 106 | [106-assets-and-js-behavior-tests.md](reports/106-assets-and-js-behavior-tests.md) | 106. Контракт отдачи ассетов на всех экранах и поведение JS вместо `node --check` |
| 109 | [109-docs-revision.md](reports/109-docs-revision.md) | 109. Ревизия документации: что действует, что в архиве |
| 111 | [111-arch-drift-2026-11-02.md](reports/111-arch-drift-2026-11-02.md) | Дрейф архитектуры: 2026-11-02 |
| 112 | [112-agent-eval.md](reports/112-agent-eval.md) | 112. Замер A-0: набор из 30 вопросов и метрика качества ответов |
| 113 | [113-agent-models-budget.md](reports/113-agent-models-budget.md) | 113. Модели и бюджеты агента: дешёвый классификатор, исполнитель по замеру, лимит на чат |
| 114 | [114-data-audit.md](reports/114-data-audit.md) | Аудит данных: бюджет и занятость |
| 115 | [115-migration-numbers.md](reports/115-migration-numbers.md) | Номера миграций: 046 и 047 были заняты дважды |
| 116 | [116-vk-miniapp-second-front.md](reports/116-vk-miniapp-second-front.md) | 116. VK Mini App как второй фронт: что переносится, чего не хватает, сколько работы |
| 117 | [117-actions-inventory.md](reports/117-actions-inventory.md) | 117. Инвентарь действий: что есть в коде |
| 120 | [120-action-registry-synthesis.md](reports/120-action-registry-synthesis.md) | 120. Свод аудитов 1/4–4/4: единый реестр действий и решение «действие из любого интерфейса» |
| 121 | [121-action-interface-matrix.md](reports/121-action-interface-matrix.md) | 121. Матрица «действие участника × интерфейс» |
| 122 | [122-interfaces-gaps.md](reports/122-interfaces-gaps.md) | 122. Разрывы интерфейсов: чат против экрана |
| 124 | [124-actions-homes-and-requirements.md](reports/124-actions-homes-and-requirements.md) | 124. Дома действий и рефакторинг требований |
| 127 | [127-intent-origin.md](reports/127-intent-origin.md) | 127. Источник намерения: сообщение человека, прямой вызов или прогон |
| 130 | [130-orphan-review.md](reports/130-orphan-review.md) | 130. Ревизия сирот: 42 маршрута без оболочки и что с каждым делать |
| 131 | [131-arch-drift-2026-11-05.md](reports/131-arch-drift-2026-11-05.md) | Дрейф: красный гейт на main — I1 и I3 в dev-БД |
| 132 | [132-db-schema-single-source-plan.md](reports/132-db-schema-single-source-plan.md) | План: единый источник схемы БД (Alembic поверх `db/init/*.sql`) |
| 135 | [135-dev-transfer-data-loss.md](reports/135-dev-transfer-data-loss.md) | Перенос dev-базы 05.11.2026: что потерялось и чем починено |
| 136 | [136-gitleaks-inventory.md](reports/136-gitleaks-inventory.md) | Секреты в индексе и истории: прогон gitleaks |
| 137 | [137-public-repo-file-decisions.md](reports/137-public-repo-file-decisions.md) | Публичный репозиторий: файл → решение |
| 138 | [138-prod-history-provenance.md](reports/138-prod-history-provenance.md) | Прод и происхождение checkout: разрыва истории приватного репозитория нет |

---

## Архив

*устаревшее и историческое; не правило, а запись* · правило пополнения — `docs/archive/README.md`

| № | Документ | О чём |
|---|---|---|
| 06 | [06-n8n-integration.md](archive/06-n8n-integration.md) | 06. Интеграция с n8n: контракты и воркфлоу |
| 15 | [15-checkpoint.md](archive/15-checkpoint.md) | 15. (исторический срез на 26.10.2026, до спринтов 1–2) — актуальное состояние в `docs/plans/07` §0 и `docs/reports/27` |
| 18 | [18-mini-app.md](archive/18-mini-app.md) | 18. Telegram Mini Apps: меню, админ-панель, формы |
| 29 | [29-n8n-scenarios.md](archive/29-n8n-scenarios.md) | 29. n8n: сценарии, контракты и ручной аудит |
| 38 | [38-approvals.md](archive/38-approvals.md) | 38. Подтверждение крупных операций |
| 83 | [83-design-system.md](archive/83-design-system.md) | 83. Дизайн-язык Mini App |
| 88 | [88-n8n-security-and-efficiency.md](archive/88-n8n-security-and-efficiency.md) | 82. n8n: оценка безопасности и эффективности, и нужен ли он вообще |

---

## Правила

1. Новый документ получает следующий свободный номер и попадает в группу по смыслу.
2. Номер не меняется при переносе между группами — меняется только путь.
3. Ссылки пишутся полным путём: `docs/<группа>/<номер>-<имя>.md`.
4. Проверка автоматическая: `tests/unit/test_docs_links.py`.
5. Устаревший документ не удаляется, а переносится в `archive/` с пометкой «Заменён: …» в первой строке.
6. Документ в архиве не действует: код и тесты на него не опираются.
7. Индекс собирается скриптом `scripts/build_docs_index.py`.
