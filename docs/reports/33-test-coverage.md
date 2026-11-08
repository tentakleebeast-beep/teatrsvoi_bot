# 33. Покрытие тестами и матрица use cases

Актуально на 26.10.2026. Дополняет `docs/development/16` (стратегия тестирования) и `docs/development/28` (use cases).

## 1. Как воспроизвести

```bash
cd "teatrsvoi_bot Project"
env -u PYTHONPATH .venv/bin/python -m pytest -p no:warnings
env -u PYTHONPATH .venv/bin/python -m pytest -q \
  --cov=services --cov=scripts --cov-report=term-missing
```

`PYTHONPATH` в окружении указывает на чужой venv, поэтому его нужно снимать.
Integration-тесты работают на БД `svoi_test` (Postgres в контейнере `svoi-postgres`,
порт 5433), схема накатывается из `db/init/*.sql`, таблицы чистятся перед каждым тестом.

## 2. Сводка

| Показатель | До аудита | После |
|---|---:|---:|
| Всего тестов | 109 | **162** |
| Passed | 109 | **162** |
| Failed | 0 | **0** |
| Покрытие `services` + `scripts` (строки) | 24% | **28%** |

Прирост — 53 теста в пяти новых integration-файлах:

| Файл | Тестов | Что закрывает |
|---|---:|---|
| `tests/integration/test_budget_summary.py` | 5 | UC-03 |
| `tests/integration/test_calendar_api.py` | 5 | UC-04 |
| `tests/integration/test_participants_api.py` | 5 | UC-06 |
| `tests/integration/test_registration.py` | 15 | UC-07, UC-08 |
| `tests/integration/test_admin_panel.py` | 15 | UC-05, UC-06, аудит, RBAC |

### Покрытие по модулям

| Модуль | Строк | Покрытие | Комментарий |
|---|---:|---:|---|
| `services/tool-api/app/rules.py` | 129 | 99% | практически всё покрыто unit-тестами |
| `services/tool-api/app/schedule_grid.py` | 194 | 87% | цветовая сетка, краевые случаи частично |
| `services/tool-api/app/main.py` | 726 | 61% | 43% → 61% после новых тестов |
| `services/tg-gateway/app/routing.py` | 21 | 100% | маршрутизация текста |
| `services/tg-gateway/app/main.py` | 926 | 19% | хендлеры бота не тестируются напрямую |
| `scripts/*` | ~1900 | 0–51% | CLI-скрипты, только helpers |
| **Итого** | **3734** | **28%** | |

Полностью не покрытые модули (0%): `scripts/bootstrap_google.py`, `build_reports.py`,
`build_router_workflow.py`, `calendar_analyze.py`, `calendar_list.py`, `calendar_week.py`,
`check_invariants.py`, `check_router_scenarios.py`, `fill_budget_sheet.py`,
`fill_test_schedule.py`, `find_schedule_file.py`, `find_shared_files.py`, `import_finance.py`,
`import_schedule.py`, `inspect_schedule_xlsx.py`, `inspect_source.py`, `intent_report.py`,
`parse_colored_schedule.py`.
Частично: `scripts/reconcile.py` 19%, `scripts/compose_manifest.py` 35%,
`scripts/calendar_fill_draft.py` 51%.

## 3. Матрица UC → тесты

| UC | Тесты | Состояние |
|---|---|---|
| UC-01 запись расхода | `unit/test_rules_budget.py` (25), `unit/test_cascade.py` (8), `unit/test_idempotency_key.py` (4), `integration/test_budget_idempotent.py` (6), `integration/test_intent_log.py` (4) | покрыт |
| UC-02 занятость | `unit/test_rules_availability.py` (9), `unit/test_schedule_grid.py` (13) | покрыт |
| UC-03 баланс и итоги | `integration/test_budget_summary.py` (5) — **новый** | покрыт контракт API: пустая база, несколько периодов, взносы, фильтр по проекту, 403 без `budget.read` |
| UC-04 расписание недели | `integration/test_calendar_api.py` (5) — **новый**; `unit/test_calendar_mask.py` (11) | покрыты ветки «не настроено» / «нет доступа» / нет прав; успешное чтение Google — нет (см. §4) |
| UC-05 админ-панель | `unit/test_gateway_routing.py` (7), `unit/test_text_routing.py` (8), `integration/test_admin_panel.py` (15) — **новый** | порядок хендлеров, 403 без прав |
| UC-06 участники и заявки | `integration/test_participants_api.py` (5), `integration/test_admin_panel.py` (15) — **новые** | список без архивных, `include_archived`, права, pending/approve/reject, аудит |
| UC-07 код приглашения | `integration/test_registration.py` (15) — **новый** | роли org/troupe, admin → 400, student → 422, отказ без `registration.code.issue` |
| UC-08 регистрация | `integration/test_registration.py` (15) — **новый** | валидный/просроченный/несуществующий код, привязка chat_id, защита от перезаписи, повторная регистрация |
| UC-09 категории | `unit/test_cascade.py`, `integration/test_intent_log.py` | покрыт |
| UC-10 сверка | `unit/test_reconcile_helpers.py` (11), `integration/test_admin_api.py` | покрыты helpers; `scripts/reconcile.py` целиком — нет |
| UC-11 занятость цветом | `unit/test_schedule_grid.py` (13) | покрыт |
| UC-12 ассистент | `unit/test_cascade.py`, `unit/test_gateway_routing.py` | маршрутизация покрыта; `/tools/assistant/ask` — нет |

## 4. Что осталось непокрытым и почему

1. **Успешные вызовы Google Calendar / Sheets.** Требуют реальной сервисной учётки и сети;
   в тестах сознательно проверяются только отказы (`calendar_id_missing`,
   `calendar_unavailable`) и отсутствие 500. Для успешного пути нужен fake-клиент
   или `responses`-мок Google API.
2. **Хендлеры `tg-gateway` (19%).** Это инлайн-логика aiogram: требует мока `Update`/`Message`
   и отдельного набора фикстур. Сейчас проверяется только чистая маршрутизация
   (`routing.py` — 100%) и текстовые правила.
3. **CLI-скрипты импорта/заполнения (`scripts/*`, 0%).** Работают с файлами и Google,
   запускаются вручную. Для них нужны фикстуры `.xlsx`; helpers (`schedule_grid`,
   reconcile) уже покрыты.
4. **`/tools/assistant/ask`.** Зависит от LLM; детерминированную ветку можно проверить
   при отсутствии ключа, но контракт ответа пока не зафиксирован.
5. **Успешная запись в Google Sheets из `budget/commit`.** В тестах
   `BUDGET_SPREADSHEET_ID=""`, поэтому проверяется только запись в БД и ответ
   «в таблицу не попало».

## 5. Найденные дефекты и расхождения (код не исправлялся)

**D1. Дублирование определений эндпоинтов в `services/tool-api/app/main.py`.**
Блоки `parse_availability_text`, `availability_parse`, `availability_commit`,
`availability_summary`, `dues_me`, `budget_parse` определены дважды (строки ~741–890
и ~892–1040). FastAPI регистрирует оба маршрута, поэтому первая версия «выигрывает»,
а вторая становится мёртвым кодом. Поведение зависит от порядка декораторов — это
риск при будущих правках. Рекомендация: удалить дубликат, добавить тест на уникальность
`(method, path)` в `app.routes`.

**D2. Расхождение путей в документации.**
В постановке и части docs упоминается `/auth/check_code`, фактический маршрут —
`POST /tools/participants/check_code` (и `/tools/participants/attach`). Тесты
закрепляют фактический контракт; документацию стоит привести к нему.

**D3. `budget/summary`: взносы без операций теряются.**
Взносы (`dues_charge`) прибавляются только к периодам, по которым есть хотя бы одна
строка `budget_tx` (цикл идёт по результату агрегата). Месяц, где были только взносы,
в итоги не попадёт. Это согласуется с UC-03 E2 («без нулей-заглушек»), но стоит
зафиксировать как осознанное решение или добавить `full outer join`.

**Замечание (не дефект). Роль `student` удалена миграцией `017_drop_student_role.sql`**:
носители переведены в `troupe`, CHECK `participant_role` сужен до `admin/org/troupe`.
Поэтому `student` в `/admin/invite` и `/admin/grant` даёт 422 — это ожидаемо и закреплено
тестами. Остался косметический след: CHECK в `invite_code` (`001_schema.sql`) всё ещё
допускает `student`, но API такое значение не пропускает.

## 6. Приоритеты

1. **P0** — убрать дубли эндпоинтов (D1): это риск для продакшена, а не только
   для покрытия.
2. **P1** — успешные ветки Google Calendar/Sheets через моки: без них UC-01 E3 и UC-04
   основного потока не проверяются.
3. **P2** — `/tools/assistant/ask` и хендлеры gateway (мок Update): UC-12 и UC-05 E2.
4. **P3** — CLI-скрипты импорта: достаточно 1–2 тестов на чтение `.xlsx`-фикстуры,
   остальное прикрыто unit-тестами helpers.
