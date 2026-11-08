# Публичный репозиторий: файл → решение

**Дата:** 06.11.2026. **Карта:** `t_16e70a41` (шаг Ш-01 плана 129). **Основание:**
`docs/plans/129-public-repo-plan.md` §Ш-01, решения Ш-00: B (сжатая история), B + C
(обезличить идентификаторы, дампы удалить), A (ротация секретов после флипа).

**Что это:** инвентарь, а не правка. Ни один файл из таблиц на шаге Ш-01 не изменён и не
удалён — решения исполняют Ш-02 (удаление дампов), Ш-03 (обезличивание), Ш-04
(инфраструктура), Ш-05 (публикуемое дерево). **Значений в отчёте нет:** ни chat_id, ни
адресов, ни имён — только пути и способ решения.

## 1. Как считали

| Категория | Метод | Файлов |
|---|---|---:|
| Дампы данных | прямые пути из плана 129 | 2 |
| Имена участников | имена из `config/participant-mapping.yaml` (обе формы: «Имя Фамилия» и «Фамилия Имя»), искомое — два слова рядом в одном отслеживаемом файле | 64 |
| chat_id владельца | поиск значения по всем отслеживаемым файлам | 51 |
| IP и домен прода | поиск значений по всем отслеживаемым файлам | 28 |
| Адрес сервисного аккаунта Google | поиск значения по всем отслеживаемым файлам | 4 |
| Суммы (денежные значения) | число с `₽`/`руб` или с разрядами и копейками; по критерию 105 файлов, в таблицах §10 разобраны 56 из них — 54 документа `docs/` и два прототипа вне `docs/`, где сумма стоит рядом с именем; остальные 49 файлов вне `docs/` несут суммы без имён | 56 |

Считали по индексу (`git ls-files`, 921 файл на момент среза), рабочий каталог не учитывали: правило
касается того, что публикуется. Всего в таблицах **173 различных файла** (категории
пересекаются: 9 файлов с именами содержат и chat_id, часть файлов с суммами входит в
категорию имён).

**Граница актуальности:** срез снят 06.11.2026 до того, как в `main` влили Ш-03
(коммит `f320638`). Поэтому строки «обезличить (Ш-03)» в §4–§7 в текущем дереве уже
исполнены, а числа в таблицах (64 файла с именами, 51 с chat_id, 4 с адресом сервисного
аккаунта, 2 с календарными id) описывают состояние до обезличивания и по текущему
дереву не воспроизводятся: это срез «что было», а не список остатка работ.

## 2. Сводка решений

| Категория | Файлов | Решение | Шаг |
|---|---:|---|---|
| Дампы данных (строки бюджета, аудита, лист «Финансы») | 2 | удалить | Ш-02 |
| Имена участников | 64 | обезличить | Ш-03 |
| chat_id владельца | 51 | обезличить | Ш-03 |
| IP прода и домен | 28 | обезличить: плейсхолдеры и `vars.DEPLOY_HOST` | Ш-04 |
| Адрес сервисного аккаунта Google | 4 | обезличить (1 файл уже с плейсхолдером) | Ш-03 |
| Суммы (денежные значения) | 56 | оставить: примеры, агрегаты и фикстуры; обезличить только 5 файлов, где сумма стоит рядом с именем (§10) | Ш-03 |
| Календарные id | 2 | оставить: плейсхолдеры и пустые значения | — |
| Почты людей в `docs/development/14` | 1 | обезличить | Ш-03 |
| Телефоны в тестах | 2 | оставить: вымышленные, пометить фикстуры | Ш-03 |
| dev-значения в `.env.example` | 1 | оставить: назвать dev-only | Ш-04 |
| Совпадения gitleaks | 2 | оставить: ложные (`136-gitleaks-inventory.md`) | — |

## 3. Дампы данных — удалить (Ш-02)

| Файл | Что внутри | Решение |
|---|---|---|
| `docs/reports/dev-db-test-rows-backup-31.10.2026.json` | строки `budget_tx` и `audit_event` тестового прогона, идентификаторы | удалить (Ш-02) |
| `docs/reports/finance-sheet-backup-28.10.2026.json` | 135 записей листа «Финансы» | удалить (Ш-02) |

Оба файла остаются во внутренней истории (bundle Ш-05); в публикуемое дерево не попадают.

## 4. Имена участников — обезличить (Ш-03)

Критерий: в файле встречается полное имя участника (два слова рядом) из маппинга.
Одиночные слова (распространённое имя в обращении, слово «тишина» в прозе) в критерий не
входят: поиск по ним даёт 141 файл, из них большинство — совпадения с общеупотребительными
словами.

| Файл | Вхождений полного имени | Решение |
|---|---:|---|
| `docs/reports/finance-sheet-backup-28.10.2026.json` | 59 | удалить (Ш-02): файл исчезает целиком |
| `config/participant-mapping.yaml` | 45 | обезличить (Ш-03) |
| `docs/archive/prototypes/telegram-miniapp.html` | 17 | обезличить (Ш-03) |
| `docs/architecture/52-twins-teachers-director.md` | 12 | обезличить (Ш-03) |
| `scripts/fill_test_schedule.py` | 10 | обезличить (Ш-03) |
| `tests/unit/test_month_grid.py` | 10 | обезличить (Ш-03) |
| `tests/unit/test_schedule_sheet.py` | 10 | обезличить (Ш-03) |
| `tests/integration/test_registration.py` | 9 | обезличить (Ш-03) |
| `docs/development/31-user-guide.md` | 8 | обезличить (Ш-03) |
| `scripts/import_schedule.py` | 8 | обезличить (Ш-03) |
| `tests/integration/test_find_person.py` | 8 | обезличить (Ш-03) |
| `tests/unit/test_person_key.py` | 8 | обезличить (Ш-03) |
| `db/init/004_people_fix.sql` | 7 | обезличить (Ш-03) |
| `docs/architecture/51-assignment-implementation.md` | 7 | обезличить (Ш-03) |
| `docs/development/43-registration-questionnaire.md` | 7 | обезличить (Ш-03) |
| `prototypes/ux2-mini-app.html` | 7 | обезличить (Ш-03) |
| `tests/integration/test_person_key_sql.py` | 7 | обезличить (Ш-03) |
| `tests/unit/test_gateway_registration.py` | 7 | обезличить (Ш-03) |
| `docs/architecture/10-data-model-v2.md` | 6 | обезличить (Ш-03) |
| `docs/development/13-registration.md` | 6 | обезличить (Ш-03) |
| `docs/reports/114-data-audit.md` | 6 | обезличить (Ш-03) |
| `docs/archive/prototypes/82-navigation-bottom-tabs.html` | 5 | обезличить (Ш-03) |
| `tests/integration/test_events_assign.py` | 5 | обезличить (Ш-03) |
| `docs/development/23-ux-scenarios.md` | 4 | обезличить (Ш-03) |
| `docs/development/53-disciplines-and-search-form.md` | 4 | обезличить (Ш-03) |
| `tests/unit/test_person_match.py` | 4 | обезличить (Ш-03) |
| `tests/unit/test_read_finance_sheet.py` | 4 | обезличить (Ш-03) |
| `tests/webapp/server.py` | 4 | обезличить (Ш-03) |
| `bot/manifests/roles/admin.md` | 3 | обезличить (Ш-03) |
| `db/init/001_schema.sql` | 3 | обезличить (Ш-03) |
| `db/init/011_archive_people.sql` | 3 | обезличить (Ш-03) |
| `docs/development/20-scenarios.md` | 3 | обезличить (Ш-03) |
| `tests/integration/test_events_disambiguation.py` | 3 | обезличить (Ш-03) |
| `tests/integration/test_webapp_month_grid.py` | 3 | обезличить (Ш-03) |
| `tests/unit/test_intro.py` | 3 | обезличить (Ш-03) |
| `tests/unit/test_subject.py` | 3 | обезличить (Ш-03) |
| `db/init/008_rbac_capabilities.sql` | 2 | обезличить (Ш-03) |
| `docs/architecture/50-event-assignment.md` | 2 | обезличить (Ш-03) |
| `docs/reports/74-questions.md` | 2 | обезличить (Ш-03) |
| `scripts/import_students_schedule.py` | 2 | обезличить (Ш-03) |
| `services/tool-api/app/person_key.py` | 2 | обезличить (Ш-03) |
| `tests/integration/test_availability_sheet.py` | 2 | обезличить (Ш-03) |
| `db/init/027_staff_roles.sql` | 1 | обезличить (Ш-03) |
| `db/init/035_participant_role_backfill.sql` | 1 | обезличить (Ш-03) |
| `db/init/041_person_key_full.sql` | 1 | обезличить (Ш-03) |
| `docs/architecture/42-window-policies.md` | 1 | обезличить (Ш-03) |
| `docs/archive/29-n8n-scenarios.md` | 1 | обезличить (Ш-03) |
| `docs/archive/prototypes/org-miniapp.html` | 1 | обезличить (Ш-03) |
| `docs/development/14-files-and-routing.md` | 1 | обезличить (Ш-03) |
| `docs/development/93-cjm.md` | 1 | обезличить (Ш-03) |
| `docs/reports/127-intent-origin.md` | 1 | обезличить (Ш-03) |
| `docs/reports/62-smoke-api.md` | 1 | обезличить (Ш-03) |
| `docs/reports/72-work-plan.md` | 1 | обезличить (Ш-03) |
| `scripts/agent_eval_set.json` | 1 | обезличить (Ш-03) |
| `services/tg-gateway/app/intro.py` | 1 | обезличить (Ш-03) |
| `services/tg-gateway/app/routers/menu.py` | 1 | обезличить (Ш-03) |
| `services/tool-api/app/api/people.py` | 1 | обезличить (Ш-03) |
| `services/tool-api/app/api/route.py` | 1 | обезличить (Ш-03) |
| `services/tool-api/app/person_match.py` | 1 | обезличить (Ш-03) |
| `tests/conftest.py` | 1 | обезличить (Ш-03) |
| `tests/integration/test_working_day_and_alerts.py` | 1 | обезличить (Ш-03) |
| `tests/unit/test_sync_finance_sheet.py` | 1 | обезличить (Ш-03) |
| `tests/webapp/test_miniapp_js_behavior.py` | 1 | обезличить (Ш-03) |
| `tests/webapp/test_miniapp_notes.py` | 1 | обезличить (Ш-03) |

Строка `config/participant-mapping.yaml` — источник имён, а не исключение: в публичном виде
пары «имя в файле → участник» тоже заменяются плейсхолдерами.

**Состав 64 строк.** 61 файл — полное имя из `config/participant-mapping.yaml` в любой форме
(«Имя Фамилия» / «Фамилия Имя»), два слова рядом. Ещё два файла содержат настоящие ФИО
персонала, которых в маппинге участников нет: `db/init/027_staff_roles.sql` (два сотрудника —
режиссёр и педагог по музыке) и `tests/integration/test_working_day_and_alerts.py` (ФИО
сотрудника в тестовой фикстуре). Плюс `docs/reports/72-work-plan.md`: там встречается только
фамилия участника в прозе разбора (причина №26), и файл оставлен в таблице, чтобы Ш-03 не
пропустил эту строку. Сумма 61 + 2 + 1 = 64.

Строка `docs/reports/64-critics-and-debts.md`, стоявшая здесь в первой редакции, убрана:
совпадение было с прозой «нажимаешь — тишина» (строка 14), полного имени в файле нет. Поэтому
итог и изменился с 65 на 64.

**Сверка с планом.** В плане 129 стоит оценка «34 файла». Факт по этому критерию — 64.
Число 34 в плане появилось как прикидка до инвентаря; правку плана делает Ш-03 (в её теле
«34 файла» уже названо), здесь фиксируется расхождение, а не подгонка. Диапазон возможных
толкований: 61 файл — если искать только имена из маппинга (обе формы, два слова рядом);
141 — если искать любое слово из 29 имён маппинга. Второй вариант не взят: большинство
совпадений там — общеупотребительные слова (например, «тишина» в прозе или распространённое
имя в обращении).

## 5. chat_id владельца — обезличить (Ш-03)

| Файл | Вхождений | Решение |
|---|---:|---|
| `docs/reports/dev-db-test-rows-backup-31.10.2026.json` | 53 | удалить (Ш-02): файл исчезает целиком |
| `tests/integration/test_agent_ask.py` | 10 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/unit/test_webapp_auth.py` | 7 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_budget_recent_period.py` | 5 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_slots_events.py` | 4 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_venue_api.py` | 4 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_venues_api.py` | 4 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `db/init/008_rbac_capabilities.sql` | 3 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_budget_idempotent.py` | 3 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_intent_log.py` | 3 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_registration.py` | 3 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `.github/workflows/probe-webapp-auth.yml` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `.github/workflows/tests.yml` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `docs/infrastructure/36-notifications.md` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `docs/reports/131-arch-drift-2026-11-05.md` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `scripts/ops/notify_owner.py` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_admin_panel.py` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_disciplines.py` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_events_assign.py` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_events_choose.py` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_events_disambiguation.py` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_howto.py` | 2 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `.github/workflows/check-menu.yml` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `.github/workflows/diagnose.yml` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `.github/workflows/notify-owner.yml` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `docs/reports/127-intent-origin.md` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `docs/reports/62-smoke-api.md` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `docs/reports/72-work-plan.md` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `scripts/agent_eval_set.json` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `scripts/check_agent_features.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `scripts/check_agent_questions.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/conftest.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_admin_archive_chat.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_admin_invites.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_admin_people_actions.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_agent_confirm.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_availability_summary.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_budget_summary.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_category_direction.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_feedback.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_feedback_digest.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_feedback_triage.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_sheet_retry.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_sheet_state.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_smoke_fixes.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_uc_admin_flows.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_uc_budget_schedule.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_webapp_archive.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/integration/test_webapp_home.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/unit/test_agent_masking.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |
| `tests/unit/test_run_service_env.py` | 1 | обезличить: переменная окружения или `123456789` (Ш-03) |

В `.github/workflows/*` значение сейчас захардкожено как адрес уведомлений — по Ш-03
переходит в переменную или секрет репозитория.

## 6. IP прода и домен — обезличить (Ш-04)

| Файл | IP прода | Домен прода | Решение |
|---|---|---|---|
| `.env.example` | — | да | оставить: пример домена в комментарии заменить на `svoi.example.ru` (Ш-04) |
| `.github/workflows/answer-feedback.yml` | да | — | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/backup-timer.yml` | да | — | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/check-menu.yml` | да | — | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/db-audit.yml` | да | — | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/deploy.yml` | да | да | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/diagnose.yml` | да | да | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/feedback-watch.yml` | да | — | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/notify-owner.yml` | да | — | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/participants-dump.yml` | да | — | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/participants-list.yml` | да | — | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/people-map.yml` | да | — | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/probe-webapp-auth.yml` | да | — | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/repair-schema.yml` | да | да | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/set-env-value.yml` | да | да | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `.github/workflows/set-llm-key.yml` | да | да | хост из `vars.DEPLOY_HOST`; в тексте — `203.0.113.10` и `svoi.example.ru` (Ш-04) |
| `docs/archive/83-design-system.md` | — | да | плейсхолдеры `203.0.113.10` и `svoi.example.ru` с пометкой «пример» (Ш-04) |
| `docs/infrastructure/35-deploy-runbook.md` | да | да | плейсхолдеры `203.0.113.10` и `svoi.example.ru` с пометкой «пример» (Ш-04) |
| `docs/infrastructure/75-hosting-and-migration.md` | да | да | плейсхолдеры `203.0.113.10` и `svoi.example.ru` с пометкой «пример» (Ш-04) |
| `docs/reports/116-vk-miniapp-second-front.md` | — | да | плейсхолдеры `203.0.113.10` и `svoi.example.ru` с пометкой «пример» (Ш-04) |
| `docs/reports/78-release-state.md` | — | да | плейсхолдеры `203.0.113.10` и `svoi.example.ru` с пометкой «пример» (Ш-04) |
| `docs/reports/80-manual-testing-night.md` | — | да | плейсхолдеры `203.0.113.10` и `svoi.example.ru` с пометкой «пример» (Ш-04) |
| `docs/reports/96-kanban-audit-31.10.2026.md` | — | да | плейсхолдеры `203.0.113.10` и `svoi.example.ru` с пометкой «пример» (Ш-04) |
| `scripts/check_availability.py` | — | да | значение из окружения, в коде — плейсхолдер `203.0.113.10` (Ш-04) |
| `scripts/feedback_sync.py` | — | да | значение из окружения, в коде — плейсхолдер `203.0.113.10` (Ш-04) |
| `tests/integration/test_webapp_boot.py` | — | да | значение из окружения, в коде — плейсхолдер `203.0.113.10` (Ш-04) |
| `tests/unit/test_availability.py` | да | да | значение из окружения, в коде — плейсхолдер `203.0.113.10` (Ш-04) |
| `tests/unit/test_check_invariants_deploy.py` | да | — | значение из окружения, в коде — плейсхолдер `203.0.113.10` (Ш-04) |

Правило: в workflow хост берётся из `vars.DEPLOY_HOST`; в текстах и примерах —
`203.0.113.10` и `svoi.example.ru` из диапазонов RFC 5737 / RFC 2606, чтобы было видно,
что это пример.

## 7. Адрес сервисного аккаунта Google — обезличить (Ш-03)

| Файл | Решение |
|---|---|
| `docs/architecture/30-architecture-c4.md` | обезличить адрес сервисного аккаунта (Ш-03) |
| `docs/development/14-files-and-routing.md` | обезличить адрес сервисного аккаунта (Ш-03) |
| `docs/development/22-schedule-fill-rules.md` | обезличить адрес сервисного аккаунта (Ш-03) |
| `docs/infrastructure/09-google-setup.md` | оставить: адрес уже в форме плейсхолдера `svoi-tool-api@<project-id>.iam.gserviceaccount.com` |

## 8. Календарные id — оставить

| Файл | Что | Решение |
|---|---|---|
| `.env.example` | `CALENDAR_ID*` пустые, значения не заданы | оставить |
| `docs/infrastructure/09-google-setup.md` | пример `abc123@group.calendar.google.com` | оставить |

## 9. Почты людей, телефоны, dev-значения

| Файл | Что | Решение |
|---|---|---|
| `docs/development/14-files-and-routing.md` | три личных адреса почты владельцев таблиц | обезличить (Ш-03) |
| `tests/unit/test_intro.py` | телефоны `+7 917 123-45-67` и подобные | оставить: вымышленные; пометить фикстуры (Ш-03) |
| `tests/integration/test_phone_privacy.py` | те же вымышленные номера | оставить: вымышленные; пометить фикстуры (Ш-03) |
| `.env.example` | `svoi_dev_password`, `dev-token`, `postgresql://svoi:***@127.0.0.1` | оставить: dev-значения, назвать dev-only и сверить с боевыми фактом (Ш-04) |

## 10. Суммы — оставить, кроме пяти файлов (Ш-03)

План 129 §0.2 считает «суммы (48 документов)» и требует обезличивать «суммы и даты, по
которым человек опознаётся». Проверили оба смысла отдельно.

**Критерий.** Денежное значение — число с `₽`/`руб` либо с разрядами и копейками
(`1 234,56`). По индексу таких файлов 105 (всего 921 файл): под `docs/` — 54, из них
**48 markdown-документов**, что совпадает с оценкой плана, плюс 4 html-прототипа и 2 json-отчёта;
ещё 51 файл — код, тесты, манифесты и прототипы вне `docs/`.

**Что действительно персональные данные.** Только сумма, стоящая рядом с именем или фамилией
участника (в пределах двух строк). Таких файлов пять:

| Файл | Что рядом | Решение |
|---|---|---|
| `docs/development/14-files-and-routing.md` | суммы в таблицах рядом с ФИО и почтами | обезличить вместе с именами (Ш-03) |
| `docs/architecture/03-budget.md` | примеры операций с именами | обезличить вместе с именами (Ш-03) |
| `docs/archive/prototypes/82-navigation-bottom-tabs.html` | макет с суммами и именем | обезличить вместе с именами (Ш-03) |
| `prototypes/ux2-mini-app.html` | макет с суммами и именем | обезличить вместе с именами (Ш-03) |
| `bot/manifests/roles/org.md` | сумма в примере рядом с фамилией | обезличить вместе с именами (Ш-03) |

Остальные суммы персональных данных не несут: это примеры в документации, агрегаты бюджета
(`docs/reports/114-data-audit.md`), стоимость токенов и моделей (`docs/reports/113`), суммы в
тестовых фикстурах. Их оставляем, иначе Ш-03 будет править сотню файлов ради чисел, по которым
никого не опознать.

Ниже — все 54 документа `docs/` с суммами, чтобы Ш-03 работал по списку, а не вслепую.

| Файл | Строк с суммами | Решение |
|---|---:|---|
| `docs/architecture/03-budget.md` | 3 | обезличить вместе с именами (Ш-03) |
| `docs/architecture/05-notifications-and-audit.md` | 2 | оставить: пример или агрегат |
| `docs/architecture/11-business-rules.md` | 1 | оставить: пример или агрегат |
| `docs/architecture/17-business-algorithms.md` | 1 | оставить: пример или агрегат |
| `docs/architecture/25-architecture-evolution.md` | 1 | оставить: пример или агрегат |
| `docs/architecture/51-assignment-implementation.md` | 1 | оставить: пример или агрегат |
| `docs/archive/15-checkpoint.md` | 1 | оставить: пример или агрегат |
| `docs/archive/38-approvals.md` | 1 | оставить: пример или агрегат |
| `docs/archive/83-design-system.md` | 1 | оставить: пример или агрегат |
| `docs/archive/prototypes/82-navigation-bottom-tabs.html` | 1 | обезличить вместе с именами (Ш-03) |
| `docs/archive/prototypes/chat-menu.html` | 1 | оставить: пример или агрегат |
| `docs/archive/prototypes/org-miniapp.html` | 1 | оставить: пример или агрегат |
| `docs/archive/prototypes/telegram-miniapp.html` | 5 | оставить: пример или агрегат |
| `docs/development/108-agent-mode.md` | 5 | оставить: пример или агрегат |
| `docs/development/12-bot-behavior.md` | 1 | оставить: пример или агрегат |
| `docs/development/14-files-and-routing.md` | 5 | обезличить вместе с именами (Ш-03) |
| `docs/development/19-functional-requirements.md` | 1 | оставить: пример или агрегат |
| `docs/development/20-scenarios.md` | 3 | оставить: пример или агрегат |
| `docs/development/23-ux-scenarios.md` | 12 | оставить: пример или агрегат |
| `docs/development/31-user-guide.md` | 18 | оставить: пример или агрегат |
| `docs/development/55-requirements-review.md` | 1 | оставить: пример или агрегат |
| `docs/development/70-obstacles.md` | 1 | оставить: пример или агрегат |
| `docs/development/82-ux-navigation.md` | 1 | оставить: пример или агрегат |
| `docs/development/91-design-system.md` | 1 | оставить: пример или агрегат |
| `docs/development/92-navigation-model.md` | 1 | оставить: пример или агрегат |
| `docs/development/93-cjm.md` | 3 | оставить: пример или агрегат |
| `docs/infrastructure/08-infrastructure.md` | 2 | оставить: пример или агрегат |
| `docs/infrastructure/24-infrastructure-deploy.md` | 4 | оставить: пример или агрегат |
| `docs/infrastructure/75-hosting-and-migration.md` | 18 | оставить: пример или агрегат |
| `docs/plans/07-roadmap-metrics.md` | 3 | оставить: пример или агрегат |
| `docs/plans/89-autonomous-development.md` | 2 | оставить: пример или агрегат |
| `docs/plans/94-ux-ui-rework-plan.md` | 1 | оставить: пример или агрегат |
| `docs/reports/106-assets-and-js-behavior-tests.md` | 1 | оставить: пример или агрегат |
| `docs/reports/112-agent-eval-run-01.11.2026.json` | 12 | оставить: пример или агрегат |
| `docs/reports/112-agent-eval.md` | 2 | оставить: пример или агрегат |
| `docs/reports/113-agent-models-budget.md` | 9 | оставить: пример или агрегат |
| `docs/reports/114-data-audit.md` | 8 | оставить: пример или агрегат |
| `docs/reports/121-action-interface-matrix.json` | 1 | оставить: пример или агрегат |
| `docs/reports/135-dev-transfer-data-loss.md` | 5 | оставить: пример или агрегат |
| `docs/reports/27-audit-and-conclusions.md` | 1 | оставить: пример или агрегат |
| `docs/reports/32-requirements-audit.md` | 1 | оставить: пример или агрегат |
| `docs/reports/49-checkpoint-2026-10-27.md` | 1 | оставить: пример или агрегат |
| `docs/reports/61-session-handover.md` | 1 | оставить: пример или агрегат |
| `docs/reports/63-release-readiness.md` | 1 | оставить: пример или агрегат |
| `docs/reports/64-critics-and-debts.md` | 1 | оставить: пример или агрегат |
| `docs/reports/65-smoke-buttons.md` | 1 | оставить: пример или агрегат |
| `docs/reports/67-intent-routing.md` | 2 | оставить: пример или агрегат |
| `docs/reports/68-state-review.md` | 2 | оставить: пример или агрегат |
| `docs/reports/72-work-plan.md` | 30 | оставить: пример или агрегат |
| `docs/reports/74-questions.md` | 1 | оставить: пример или агрегат |
| `docs/reports/78-release-state.md` | 2 | оставить: пример или агрегат |
| `docs/reports/79-code-architecture-review.md` | 1 | оставить: пример или агрегат |
| `docs/reports/80-manual-testing-night.md` | 2 | оставить: пример или агрегат |
| `docs/reports/99-shared-helpers.md` | 2 | оставить: пример или агрегат |

Два дампа из §3 тоже содержат суммы (строки бюджета и записи листа «Финансы») — они удаляются
целиком по Ш-02, отдельного решения по суммам внутри них не нужно.

## 11. Совпадения gitleaks по дереву — оставить

| Файл:строка | Правило | Решение |
|---|---|---|
| `db/init/016_availability_invert.sql:20` | `generic-api-key` | оставить: имя ключа миграции, не секрет |
| `.github/workflows/tests.yml:252` | `curl-auth-header` | оставить: маска `***` вместо значения |

Подробности прогона и коммиты — `docs/reports/136-gitleaks-inventory.md`.

## 12. Чего этот шаг не делает

- Не удаляет дампы и не правит файлы: это Ш-02 и Ш-03.
- Не трогает историю и не собирает bundle: это Ш-05.
- Не решает, какие секреты ротировать: решение Ш-00 п.4 — все, процедура в Ш-08.
- Не добавляет `.gitleaksignore`: два ложных совпадения по дереву описаны, конфиг — отдельная работа.
