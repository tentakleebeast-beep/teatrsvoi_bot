# 117. Инвентарь действий: что есть в коде

Карта: `t_08b6ae7a` (аудит 2/4). Документ собран скриптом `scripts/audit_actions.py` по файлам репозитория; таблицы между маркерами переписываются командой и руками не правятся — `--check` падает при расхождении.

Источники: маршруты — `app.openapi()` собранного tool-api, сверенные с `contracts/openapi.json`; права — зависимости маршрутов и тела обработчиков (`core._webapp_require`, сверка Bearer-токена); потребители — литералы путей в `app/pages/*.py`, `app/static/*.js`, `services/tg-gateway/**`; тесты — вызовы путей в `tests/**`; инструменты агента — закрытый реестр `app/agently/registry.py`.

Правило проекта (docs/development/86): у Mini App нет своего API, экраны зовут те же эндпоинты, что и бот. Поэтому «действие» — это маршрут tool-api, а интерфейсы (чат, экран, скрипт) — оболочки над ним.

## Что видно из таблиц

Наблюдения на 04.11.2026; их подтверждают таблицы ниже. При изменении кода таблицы обновляются командой, а этот раздел остаётся записью среза.

* **Одни эндпоинты — несколько оболочек.** Экраны Mini App и бот зовут общий API: у домена занятости есть пары `/tools/availability/*` и `/webapp/availability/*`, и потребители расходятся по половинам пары. Половина без потребителя — в §8.
* **Действия-сироты с тестами.** Отмена и перенос события (`/tools/events/cancel`, `/tools/events/move`) объявлены в API и покрыты тестами, но ни экрана, ни кнопки у них нет: тест проверяет то, чем никто не пользуется.
* **В домене файлов два слоя.** Чат ходит в `/files/…` и `/folders/…`, экран — в `/webapp/files/*` и `/webapp/folder/*`. Часть маршрутов прежнего слоя (`/folders/{id}/…`, `/files/{id}/access`) не зовёт никто.
* **Права проверяются тремя путями.** `core.require(…)` в зависимости маршрута — правило в одном месте; `core._webapp_require(who, "…")` в теле 28 обработчиков Mini App — та же проверка, вызванная напрямую; `"*" in caps or "<право>" in caps` в теле — третий путь. Отдельно на входе стоит сверка служебного Bearer-токена (2 маршрута для другого сервиса). Списки — §7.
* **Публичные экраны — правило, а не дефект.** HTML страниц, SDK и CSS отдаются без сессии, данные проверяются в API; регистрация и вход по определению идут до неё. Дефект — действие без права, которое меняет данные.

Воспроизведение:

```
env -u PYTHONPATH .venv/bin/python scripts/audit_actions.py --json   # машинный вид
env -u PYTHONPATH .venv/bin/python scripts/audit_actions.py --check  # отчёт = код
env -u PYTHONPATH .venv/bin/python scripts/audit_actions.py --write  # переписать
```

<!-- audit-actions:begin -->
## 1. Сводка

| Показатель | Значение |
|---|---|
| Маршрутов (методов) в `contracts/openapi.json` | 264 |
| Маршрутов в коде, которые есть в контракте | 264 |
| Всего маршрутов в коде (с редиректами и ресурсами) | 265 |
| Из них с тестом (по вызову пути в `tests/`) | 253 |
| Действий без права (ни возможности, ни сессии) | 53 |
| Действий с правом, проверенным в теле через `core._webapp_require(…)` | 64 |
| Действий с правом, проверенным в теле через «*» в caps | 3 |
| Маршрутов с сервисным Bearer-токеном на входе | 3 |
| Эндпоинтов, которые не зовёт ни один интерфейс | 35 |
| Экранов Mini App | 35 |
| Обработчиков чата (команды, кнопки, тексты) | 122 |
| Инструментов агента | 13 |

## 2. tool-api: маршруты и права

Права проверяются тремя путями. Первый — зависимость маршрута `Depends(core.require("…"))` (конкретная возможность); вход в сессию объявляют `core.current` (любой активный участник) и `core.webapp_actor` (сессия Mini App, подпись `initData`). Второй — `core._webapp_require(who, "…")` в теле обработчика: та же проверка, что `require()`, но вызванная напрямую (28 маршрутов Mini App, §7a). Третий — `"*" in caps` в теле обработчика (2 маршрута, §7c). Отдельно от прав, на входе, стоит проверка служебного Bearer-токена: `hmac.compare_digest(…, f"Bearer {TOOL_API_TOKEN}")` у маршрутов для другого сервиса (2, §7b). «—» — ни одного из этих путей нет.

| Действие | Точка входа | Интерфейсы | Право | Тест | Примечание |
|---|---|---|---|---|---|
| POST `/admin/archive` | `services/tool-api/app/api/admin.py:1399` | чат: panel_person_archive, panel_rows | participants.manage | test_admin_archive_chat.py, test_admin_people_actions.py, test_cm01_login.py (+1) | — |
| POST `/admin/block` | `services/tool-api/app/api/admin.py:1528` | чат: _block_person | participants.manage | test_admin_people_actions.py, test_cm01_login.py, test_gateway_chat_parity.py | — |
| POST `/admin/bootstrap` | `services/tool-api/app/api/admin.py:105` | чат: start | сервисный токен | нет | проверка на входе — сервисный Bearer-токен, а не сессия человека |
| GET `/admin/capabilities` | `services/tool-api/app/api/admin.py:423` | чат: panel_person_caps, panel_rights | participants.view | test_admin_panel.py | — |
| POST `/admin/files/cold-rotate` | `services/tool-api/app/api/admin.py:1694` | — | сервисный токен | test_cold.py, test_service_routes_auth.py | проверка на входе — сервисный Bearer-токен, а не сессия человека; никто не зовёт |
| GET `/admin/files/storage` | `services/tool-api/app/api/admin.py:1668` | — | сервисный токен | test_cold.py, test_service_routes_auth.py | проверка на входе — сервисный Bearer-токен, а не сессия человека; никто не зовёт |
| POST `/admin/files/{file_id}/access` | `services/tool-api/app/api/admin.py:1652` | чат: cmd_access | любой активный | test_file_access.py, test_files_actions_screen.py, test_files_studio_roles.py (+2) | — |
| POST `/admin/grant` | `services/tool-api/app/api/admin.py:466` | чат: panel_person_cap_toggle, panel_person_master_grant, panel_person_role_toggle, panel_rights, panel_rows | participants.manage | test_admin_panel.py, test_admin_people_actions.py, test_gateway_chat_parity.py (+1) | — |
| POST `/admin/participant/discipline` | `services/tool-api/app/api/admin.py:557` | чат: panel_person_master_link | participants.manage | test_gateway_chat_parity.py, test_master_role.py | — |
| POST `/admin/reconcile` | `services/tool-api/app/api/admin.py:837` | чат: panel_reconcile, reconcile_apply, reconcile_apply_panel, start_reconcile | budget.import | test_admin_api.py | — |
| POST `/admin/unarchive` | `services/tool-api/app/api/admin.py:1451` | чат: panel_person_unarchive | participants.manage | test_admin_archive_chat.py, test_admin_people_actions.py, test_cm01_login.py (+1) | — |
| POST `/agent/actions/{action_id}/apply` | `services/tool-api/app/api/admin.py:1766` | чат: agent_action_cb (по шаблону) | любой активный | нет | — |
| POST `/agent/actions/{action_id}/reject` | `services/tool-api/app/api/admin.py:1778` | чат: agent_action_cb (по шаблону) | любой активный | нет | — |
| GET `/files` | `services/tool-api/app/api/files.py:97` | чат: cmd_files, cmd_search; скрипты: neighbor_counts, restore_dump, tool_list_files (по шаблону) | любой активный | test_action_registry.py, test_files_actions_screen.py, test_files_delete.py (+6) | — |
| POST `/files/upload` | `services/tool-api/app/api/files.py:49` | чат: _delete_card, file_delete_confirm, file_send, upload_to_storage (по шаблону) | любой активный | test_cm07_files.py, test_cold.py, test_feedback_attachment.py (+10) | — |
| DELETE `/files/{file_id}` | `services/tool-api/app/api/files.py:125` | чат: _delete_card, file_delete_confirm, file_send, upload_to_storage | любой активный | test_cm07_files.py, test_cold.py, test_feedback_attachment.py (+11) | — |
| GET `/files/{file_id}` | `services/tool-api/app/api/files.py:111` | чат: _delete_card, file_delete_confirm, file_send, upload_to_storage | любой активный | test_cm07_files.py, test_cold.py, test_feedback_attachment.py (+11) | — |
| GET `/files/{file_id}/access` | `services/tool-api/app/api/files.py:291` | — | любой активный | test_file_access.py, test_files_studio_roles.py | никто не зовёт |
| POST `/files/{file_id}/access/revoke` | `services/tool-api/app/api/files.py:277` | чат: cmd_revoke | любой активный | test_file_access.py, test_files_actions_screen.py, test_gateway_files_rights.py | — |
| GET `/files/{file_id}/content` | `services/tool-api/app/api/files.py:141` | чат: file_send | любой активный | test_cm07_files.py, test_cold.py, test_file_access.py (+3) | — |
| POST `/files/{file_id}/folder` | `services/tool-api/app/api/files.py:594` | — | любой активный | test_files_actions_screen.py, test_folder_order.py, test_webapp_files_link.py (+1) | никто не зовёт |
| POST `/files/{file_id}/link` | `services/tool-api/app/api/files.py:182` | чат: file_send | любой активный | test_files_download.py, test_webapp_files_view_external.py | — |
| GET `/files/{file_id}/tags` | `services/tool-api/app/api/files.py:738` | чат: cmd_tag | любой активный | test_search_tags.py | — |
| POST `/files/{file_id}/tags` | `services/tool-api/app/api/files.py:749` | чат: cmd_tag | любой активный | test_search_tags.py | — |
| GET `/folders` | `services/tool-api/app/api/files.py:221` | чат: cmd_folders, cmd_mkdir; скрипты: neighbor_counts, restore_dump, tool_list_files (по шаблону) | любой активный | test_action_registry.py, test_files_actions_screen.py, test_folders.py (+4) | — |
| POST `/folders` | `services/tool-api/app/api/files.py:234` | чат: cmd_folders, cmd_mkdir; скрипты: neighbor_counts, restore_dump, tool_list_files (по шаблону) | любой активный | test_action_registry.py, test_files_actions_screen.py, test_folders.py (+4) | — |
| POST `/folders/{folder_id}/delete` | `services/tool-api/app/api/files.py:263` | чат: cmd_rmfolder | любой активный | test_folders.py, test_gateway_files_rights.py | — |
| GET `/folders/{folder_id}/files` | `services/tool-api/app/api/files.py:566` | — | любой активный | test_files_actions_screen.py, test_files_delete.py, test_folder_order.py | никто не зовёт |
| POST `/folders/{folder_id}/order` | `services/tool-api/app/api/files.py:582` | чат: cmd_order | любой активный | test_files_actions_screen.py, test_folder_order.py, test_gateway_files_rights.py | — |
| POST `/folders/{folder_id}/rename` | `services/tool-api/app/api/files.py:248` | чат: cmd_rename | любой активный | test_folders.py, test_gateway_files_rights.py | — |
| GET `/folders/{folder_id}/zip` | `services/tool-api/app/api/files.py:616` | чат: cmd_zip | любой активный | test_files_quota.py, test_folder_order.py, test_gateway_files_zip.py | — |
| GET `/health` | `services/tool-api/app/main.py:245` | чат: panel_health, today_local; скрипты: _declared_routes, check_availability, check_health, check_services, fetch_health, health, health_agent, install_launchd, neighbor_counts, restore_dump, start_services (по шаблону) | — | test_arch_drift.py, test_ci_images.py, test_cm01_login.py (+2) | — |
| GET `/tags` | `services/tool-api/app/api/files.py:700` | чат: cmd_tag, cmd_tags; скрипты: neighbor_counts, restore_dump (по шаблону) | любой активный | test_action_registry.py, test_files_actions_screen.py, test_gateway_files_rights.py (+1) | — |
| POST `/tags` | `services/tool-api/app/api/files.py:709` | чат: cmd_tag, cmd_tags; скрипты: neighbor_counts, restore_dump (по шаблону) | любой активный | test_action_registry.py, test_files_actions_screen.py, test_gateway_files_rights.py (+1) | — |
| POST `/tags/{tag_id}/approve` | `services/tool-api/app/api/files.py:724` | чат: cmd_tag_ok | любой активный | test_search_tags.py | — |
| POST `/tools/agent/ask` | `services/tool-api/app/api/agent_ask.py:185` | чат: agent_ask | любой активный | test_agent_ask.py, test_agent_observability.py, test_gateway_agent_ask.py | — |
| POST `/tools/agent/cancel` | `services/tool-api/app/api/agent_confirm.py:222` | чат: agent_cancel, confirm | любой активный | test_agent_confirm.py, test_gateway_agent_confirm.py | — |
| POST `/tools/agent/cancel-task` | `services/tool-api/app/api/agent_confirm.py:118` | чат: agent_cancel_task | любой активный | нет | — |
| POST `/tools/agent/confirm` | `services/tool-api/app/api/agent_confirm.py:179` | чат: agent_confirm, confirm | любой активный | test_agent_confirm.py, test_gateway_agent_confirm.py | — |
| POST `/tools/agent/files` | `services/tool-api/app/api/admin.py:1726` | — | любой активный | нет | никто не зовёт |
| POST `/tools/agent/plan` | `services/tool-api/app/api/agent_confirm.py:86` | чат: agent_plan | любой активный | test_agent_task.py | — |
| POST `/tools/agent/plan/confirm` | `services/tool-api/app/api/agent_confirm.py:96` | чат: agent_plan_confirm | любой активный | test_agent_task.py | — |
| POST `/tools/agent/propose` | `services/tool-api/app/api/agent_confirm.py:73` | чат: agent_propose | любой активный | test_agent_confirm.py | — |
| POST `/tools/assistant/ask` | `services/tool-api/app/api/admin.py:637` | чат: on_text | любой активный | test_cm09_assistant.py, test_gateway_avail_text.py | — |
| GET `/tools/audit/recent` | `services/tool-api/app/api/admin.py:136` | чат: panel_audit | audit.view | test_admin_panel.py, test_budget_idempotent.py, test_cm01_login.py | — |
| POST `/tools/availability/commit` | `services/tool-api/app/api/schedule.py:549` | чат: av_save | schedule.availability.self | test_availability_summary.py, test_availability_week.py, test_cm02_availability.py (+4) | — |
| POST `/tools/availability/parse` | `services/tool-api/app/api/schedule.py:516` | чат: busy_card | любой активный | test_cm02_availability.py, test_gateway_avail_text.py | — |
| POST `/tools/availability/parse-interval` | `services/tool-api/app/api/schedule.py:527` | чат: av_time_text | любой активный | test_gateway_avail_escape.py | — |
| POST `/tools/availability/reconcile` | `services/tool-api/app/api/schedule.py:1533` | — | schedule.availability.self (в теле — schedule.read.all) | test_availability_sheet.py | проверка права в теле обработчика (через «*»); никто не зовёт |
| POST `/tools/availability/sheet` | `services/tool-api/app/api/schedule.py:1279` | — | schedule.availability.self | test_availability_sheet.py | никто не зовёт |
| GET `/tools/availability/sheet/mismatches` | `services/tool-api/app/api/schedule.py:1490` | — | schedule.availability.self | test_availability_sheet.py | никто не зовёт |
| GET `/tools/availability/summary` | `services/tool-api/app/api/schedule.py:571` | чат: answer_schedule | schedule.read.all | test_availability_summary.py, test_cm02_availability.py, test_smoke_fixes.py (+1) | — |
| GET `/tools/availability/template` | `services/tool-api/app/api/schedule.py:941` | чат: _week_view, week_text | schedule.availability.self | test_availability_week.py, test_gateway_avail_parity.py | — |
| POST `/tools/availability/template` | `services/tool-api/app/api/schedule.py:956` | чат: _week_view, week_text | schedule.availability.self | test_availability_week.py, test_gateway_avail_parity.py | — |
| POST `/tools/availability/template/apply` | `services/tool-api/app/api/schedule.py:1011` | чат: week_action | schedule.availability.self | test_availability_week.py, test_gateway_avail_parity.py | — |
| POST `/tools/availability/template/from-week` | `services/tool-api/app/api/schedule.py:968` | чат: week_action | schedule.availability.self | test_availability_week.py, test_cm02_availability.py, test_gateway_avail_parity.py (+1) | — |
| POST `/tools/availability/test-sheet` | `services/tool-api/app/api/schedule.py:1306` | — | admin.panel | test_google_test_sheet_api.py | никто не зовёт |
| GET `/tools/availability/week` | `services/tool-api/app/api/schedule.py:742` | чат: av_save; скрипты: tool_read_availability | schedule.availability.self (в теле — schedule.read.all) | test_availability_sheet.py, test_availability_week.py, test_cm02_availability.py (+2) | проверка права в теле обработчика (через «*») |
| POST `/tools/availability/week` | `services/tool-api/app/api/schedule.py:821` | чат: av_save; скрипты: tool_read_availability | schedule.availability.self | test_availability_sheet.py, test_availability_week.py, test_cm02_availability.py (+2) | — |
| POST `/tools/budget/commit` | `services/tool-api/app/api/budget.py:265` | чат: budget_confirm, confirm, form_save | budget.write | test_budget_idempotent.py, test_budget_recent_period.py, test_category_direction.py (+6) | — |
| POST `/tools/budget/parse` | `services/tool-api/app/api/budget.py:492` | чат: parse_budget | любой активный | test_cm04_money.py, test_cm09_assistant.py, test_gateway_assign_dialog.py (+2) | — |
| GET `/tools/budget/recent` | `services/tool-api/app/api/budget.py:403` | чат: panel_money, periods | budget.read | test_budget_idempotent.py, test_budget_recent_period.py, test_smoke_api_fixes.py | — |
| POST `/tools/budget/sheet-retry` | `services/tool-api/app/api/budget.py:319` | чат: panel_sheet_retry | budget.write | test_sheet_retry.py, test_webapp_sheet_retry.py | — |
| GET `/tools/budget/summary` | `services/tool-api/app/api/budget.py:503` | чат: answer_money; скрипты: tool_read_budget | budget.read | test_budget_summary.py, test_cm04_money.py, test_cm09_assistant.py (+4) | — |
| GET `/tools/calendar/events` | `services/tool-api/app/api/events.py:929` | чат: answer_schedule; скрипты: tool_read_schedule | schedule.read.all | test_calendar_api.py, test_cm11_degradation.py, test_smoke_fixes.py (+2) | — |
| GET `/tools/calendar/health` | `services/tool-api/app/api/events.py:902` | — | любой активный | test_calendar_api.py | никто не зовёт |
| GET `/tools/categories` | `services/tool-api/app/api/budget.py:443` | чат: form_amount | любой активный | test_ci_images.py | — |
| GET `/tools/disciplines` | `services/tool-api/app/api/events.py:886` | чат: panel_person_master | любой активный | test_disciplines.py, test_gateway_chat_parity.py | — |
| POST `/tools/dues/accrue` | `services/tool-api/app/api/finance.py:65` | — | budget.write | test_cm04_money.py, test_dues_accrue.py | никто не зовёт |
| GET `/tools/dues/me` | `services/tool-api/app/api/finance.py:24` | чат: answer_profile, cmd_dues, my_dues, run_menu_action, show_dues; скрипты: tool_read_dues | budget.read.self | test_cm04_money.py, test_dues_admin_ui.py, test_dues_tariff.py (+2) | — |
| POST `/tools/events/assign` | `services/tool-api/app/api/events.py:406` | чат: _load, assign_confirm (по шаблону) | schedule.write | test_cm03_slots.py, test_event_chat_card.py, test_events_assign.py (+1) | — |
| POST `/tools/events/cancel` | `services/tool-api/app/api/events.py:605` | чат: _cancel, _load (по шаблону) | schedule.write | test_cm03_slots.py, test_event_cancel.py, test_event_chat_card.py | — |
| POST `/tools/events/choose` | `services/tool-api/app/api/events.py:360` | чат: _load, assign_pick (по шаблону) | schedule.write | test_events_choose.py | — |
| POST `/tools/events/move` | `services/tool-api/app/api/events.py:715` | чат: _load, _move (по шаблону) | schedule.write | test_cm03_slots.py, test_event_move.py | — |
| POST `/tools/events/preview` | `services/tool-api/app/api/events.py:325` | чат: _load, preview (по шаблону) | schedule.write | test_disciplines.py, test_events_assign.py, test_events_choose.py (+6) | — |
| GET `/tools/events/{event_id}` | `services/tool-api/app/api/events.py:840` | чат: _cancel, _load, _move, assign_confirm, assign_pick, preview | schedule.write | test_cm03_slots.py, test_disciplines.py, test_event_cancel.py (+10) | — |
| GET `/tools/feedback` | `services/tool-api/app/api/admin.py:307` | чат: feedback_triage, panel_feedback, send_feedback; скрипты: cmd_pull | feedback.triage | test_cm06_feedback.py, test_feedback.py, test_feedback_attachment.py (+2) | — |
| POST `/tools/feedback` | `services/tool-api/app/api/admin.py:176` | чат: feedback_triage, panel_feedback, send_feedback; скрипты: cmd_pull | любой активный | test_cm06_feedback.py, test_feedback.py, test_feedback_attachment.py (+2) | — |
| POST `/tools/feedback/decide` | `services/tool-api/app/api/admin.py:352` | чат: feedback_decide; скрипты: cmd_decide, decide | feedback.triage | test_cm06_feedback.py, test_feedback.py, test_feedback_digest.py | — |
| GET `/tools/feedback/mine` | `services/tool-api/app/api/admin.py:290` | — | любой активный | test_cm06_feedback.py | никто не зовёт |
| GET `/tools/feedback/open` | `services/tool-api/app/api/feedback.py:72` | — | feedback.triage | test_feedback_triage.py | никто не зовёт |
| POST `/tools/feedback/triage` | `services/tool-api/app/api/feedback.py:211` | скрипты: main | feedback.triage | test_feedback_triage.py | — |
| GET `/tools/feedback/{feedback_id}/attachment/{file_id}` | `services/tool-api/app/api/feedback.py:123` | скрипты: feedback_inbox | feedback.triage | test_feedback_attachment.py | — |
| GET `/tools/finance/granularity` | `services/tool-api/app/api/finance.py:190` | — | budget.read | нет | никто не зовёт |
| POST `/tools/finance/granularity` | `services/tool-api/app/api/finance.py:200` | — | budget.import | нет | никто не зовёт |
| GET `/tools/finance/sources` | `services/tool-api/app/api/finance.py:224` | — | budget.read | нет | никто не зовёт |
| POST `/tools/finance/sources` | `services/tool-api/app/api/finance.py:241` | — | budget.import | нет | никто не зовёт |
| DELETE `/tools/finance/sources/{source_id}` | `services/tool-api/app/api/finance.py:260` | — | budget.import | нет | никто не зовёт |
| GET `/tools/google/freshness` | `services/tool-api/app/api/google.py:73` | — | schedule.read.all | test_google_mode_api.py, test_google_no_pii.py | никто не зовёт |
| POST `/tools/google/mode` | `services/tool-api/app/api/google.py:65` | — | admin.panel | test_google_mode_api.py | никто не зовёт |
| POST `/tools/google/refresh` | `services/tool-api/app/api/google.py:86` | — | schedule.read.all | test_google_no_pii.py | никто не зовёт |
| GET `/tools/howto` | `services/tool-api/app/api/webapp_api.py:186` | чат: run_menu_action | howto.read | test_howto.py | — |
| GET `/tools/intent/categories` | `services/tool-api/app/api/admin.py:718` | — | любой активный | test_intent_log.py | никто не зовёт |
| GET `/tools/intent/report` | `services/tool-api/app/api/admin.py:766` | — | audit.view | test_intent_log.py, test_smoke_api_fixes.py | никто не зовёт |
| POST `/tools/intent/resolve` | `services/tool-api/app/api/admin.py:728` | — | любой активный | test_intent_log.py | никто не зовёт |
| GET `/tools/intent/sheet` | `services/tool-api/app/api/admin.py:783` | скрипты: cmd_pull | admin.panel | test_intent_sheet.py | — |
| GET `/tools/me/capabilities` | `services/tool-api/app/api/people.py:637` | чат: caps_of | любой активный | test_admin_people_actions.py, test_cm01_login.py | — |
| GET `/tools/notes` | `services/tool-api/app/api/notes.py:260` | чат: note_confirm | notes.write | test_cm07_files.py, test_cm12_audit_cleanup.py, test_gateway_notes.py (+1) | — |
| POST `/tools/notes` | `services/tool-api/app/api/notes.py:269` | чат: note_confirm | notes.write | test_cm07_files.py, test_cm12_audit_cleanup.py, test_gateway_notes.py (+1) | — |
| DELETE `/tools/notes/{note_id}` | `services/tool-api/app/api/notes.py:288` | — | notes.write | test_notes_api.py | никто не зовёт |
| POST `/tools/notes/{note_id}` | `services/tool-api/app/api/notes.py:278` | — | notes.write | test_notes_api.py | никто не зовёт |
| GET `/tools/notifications/settings` | `services/tool-api/app/api/webapp_api.py:490` | чат: _quiet_settings, _save_quiet | любой активный | test_cm05_notifications.py, test_gateway_quiet.py, test_gateway_quiet_parity.py | — |
| POST `/tools/notifications/settings` | `services/tool-api/app/api/webapp_api.py:502` | чат: _quiet_settings, _save_quiet | любой активный | test_cm05_notifications.py, test_gateway_quiet.py, test_gateway_quiet_parity.py | — |
| GET `/tools/participants` | `services/tool-api/app/api/people.py:63` | чат: _send_archive, list_people, panel_people, send_people | participants.view | test_admin_api.py, test_admin_archive_chat.py, test_cm01_login.py (+6) | — |
| POST `/tools/participants/attach` | `services/tool-api/app/api/people.py:589` | чат: _attach_by_code | — | test_admin_new_person.py, test_cm01_login.py, test_gateway_registration.py (+4) | — |
| POST `/tools/participants/check_code` | `services/tool-api/app/api/people.py:527` | чат: on_text | — | test_admin_invites.py, test_admin_new_person.py, test_gateway_registration.py (+3) | — |
| GET `/tools/participants/find` | `services/tool-api/app/api/people.py:574` | — | participants.view | test_find_person.py, test_registration.py | никто не зовёт |
| GET `/tools/participants/me` | `services/tool-api/app/api/people.py:54` | чат: about_me, answer_profile, disciplines_line, intro_cancel, intro_save, start, user_ctx | любой активный | test_gateway_chat_parity.py, test_gateway_menu_rights.py, test_intro.py (+3) | — |
| GET `/tools/participants/{participant_id}/card` | `services/tool-api/app/api/people.py:18` | чат: _card_of, panel_person, panel_person_caps, panel_person_roles, panel_person_tariff | participants.view | test_gateway_chat_parity.py, test_master_role.py, test_participants_card.py (+1) | — |
| GET `/tools/participants/{participant_id}/dues-tariff` | `services/tool-api/app/api/people.py:677` | чат: panel_tariff_save | participants.view | test_dues_tariff.py | — |
| POST `/tools/participants/{participant_id}/dues-tariff` | `services/tool-api/app/api/people.py:694` | чат: panel_tariff_save | budget.write | test_dues_tariff.py | — |
| POST `/tools/polls/answer` | `services/tool-api/app/api/polls.py:827` | чат: _answer, poll_free_text | любой активный | test_poll_text_answer.py, test_polls_answer_overwrite.py, test_polls_delivery.py (+2) | — |
| GET `/tools/polls/mine` | `services/tool-api/app/api/polls.py:792` | чат: show_runs | любой активный | test_poll_resume_chat.py, test_polls_resume.py, test_polls_webapp.py | — |
| GET `/tools/polls/step` | `services/tool-api/app/api/polls.py:809` | чат: poll_step | любой активный | test_poll_resume_chat.py, test_poll_text_answer.py, test_polls_questionnaire_161.py (+1) | — |
| POST `/tools/profile/intro-parse` | `services/tool-api/app/api/people.py:281` | чат: intro_answer | любой активный | test_intro.py, test_intro_llm_api.py | — |
| GET `/tools/profile/me` | `services/tool-api/app/api/people.py:118` | чат: intro_fill, intro_save, intro_start, profile_cmd | любой активный | test_availability_week.py, test_cm01_login.py, test_cm11_degradation.py (+4) | — |
| POST `/tools/profile/me` | `services/tool-api/app/api/people.py:191` | чат: intro_fill, intro_save, intro_start, profile_cmd | любой активный | test_availability_week.py, test_cm01_login.py, test_cm11_degradation.py (+4) | — |
| GET `/tools/quota/me` | `services/tool-api/app/api/quota.py:86` | чат: answer_quota | любой активный | test_quota_me.py | — |
| POST `/tools/route` | `services/tool-api/app/api/route.py:74` | чат: router_ask; скрипты: agent_eval, ask | любой активный | test_cm09_assistant.py, test_cm11_degradation.py, test_routes_contract.py (+1) | — |
| POST `/tools/schedule/slots` | `services/tool-api/app/api/schedule.py:253` | чат: answer_slots | schedule.slots | test_agent_slots_tool.py, test_cm03_slots.py, test_gateway_slots_entry.py (+7) | — |
| GET `/tools/ui-texts` | `services/tool-api/app/api/webapp_api.py:202` | чат: ui_texts_cached | любой активный | нет | — |
| GET `/tools/venues` | `services/tool-api/app/api/events.py:895` | — | любой активный | test_venues_api.py | никто не зовёт |
| GET `/webapp` | `services/tool-api/app/pages/routes.py:113` | Mini App: home, routes; чат: profile_cmd, run_menu_action; скрипты: build, build_screen_map, chat_entries, label_of, neighbor_counts, restore_dump, ui_review, ux_audit (по шаблону) | — | server.py, test_agent_features.py, test_miniapp_master_disciplines.py (+5) | экран Mini App: HTML без данных |
| GET `/webapp/admin` | `services/tool-api/app/pages/routes.py:225` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_static_contract.py, test_webapp_admin.py | — |
| GET `/webapp/admin/agent` | `services/tool-api/app/api/admin.py:1354` | Mini App: admin_agent | сессия Mini App + admin.panel | server.py, test_agent_observability.py | право проверяется в теле обработчика, а не в зависимости: admin.panel |
| POST `/webapp/admin/archive` | `services/tool-api/app/api/admin.py:1394` | Mini App: admin_archive, admin_people | сессия Mini App + participants.manage | test_admin_archive_chat.py, test_admin_people_actions.py, test_miniapp_archive_rights.py (+2) | право проверяется в теле обработчика, а не в зависимости: participants.manage |
| POST `/webapp/admin/block` | `services/tool-api/app/api/admin.py:1522` | Mini App: admin_people | сессия Mini App + participants.manage | test_admin_people_actions.py, test_webapp_pages_js.py | право проверяется в теле обработчика, а не в зависимости: participants.manage |
| POST `/webapp/admin/broadcast` | `services/tool-api/app/api/admin.py:1616` | Mini App: misc_broadcast | сессия Mini App + notify.send | server.py, test_broadcast_rights.py, test_cm05_notifications.py | право проверяется в теле обработчика, а не в зависимости: notify.send |
| GET `/webapp/admin/broadcast/preview` | `services/tool-api/app/api/admin.py:1598` | Mini App: misc_broadcast | сессия Mini App + notify.send | server.py, test_broadcast_rights.py, test_cm05_notifications.py (+1) | право проверяется в теле обработчика, а не в зависимости: notify.send |
| POST `/webapp/admin/discipline` | `services/tool-api/app/api/admin.py:1006` | Mini App: admin_disciplines | сессия Mini App + participants.manage | server.py, test_master_role.py | право проверяется в теле обработчика, а не в зависимости: participants.manage |
| GET `/webapp/admin/dues` | `services/tool-api/app/api/finance.py:110` | Mini App: admin_dues | сессия Mini App + budget.write | server.py, test_dues_admin_ui.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: budget.write |
| POST `/webapp/admin/dues/accrue` | `services/tool-api/app/api/finance.py:174` | Mini App: admin_dues | сессия Mini App + budget.write | server.py, test_dues_admin_ui.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: budget.write |
| GET `/webapp/admin/feedback` | `services/tool-api/app/api/admin.py:1177` | Mini App: admin_feedback | сессия Mini App + feedback.triage | server.py, test_feedback_attachment.py | право проверяется в теле обработчика, а не в зависимости: feedback.triage |
| POST `/webapp/admin/feedback/decide` | `services/tool-api/app/api/admin.py:1189` | Mini App: admin_feedback | сессия Mini App + feedback.triage | test_feedback_open_only.py, test_webapp_merge_deps.py | право проверяется в теле обработчика, а не в зависимости: feedback.triage |
| POST `/webapp/admin/grant` | `services/tool-api/app/api/admin.py:997` | Mini App: admin_people, admin_rights | сессия Mini App + participants.manage | server.py, test_master_role.py, test_miniapp_master_grant.py (+3) | право проверяется в теле обработчика, а не в зависимости: participants.manage |
| POST `/webapp/admin/invite` | `services/tool-api/app/api/admin.py:1122` | Mini App: admin_invites | сессия Mini App + registration.code.issue | server.py, test_admin_archive_chat.py, test_admin_invites.py (+9) | право проверяется в теле обработчика, а не в зависимости: registration.code.issue |
| GET `/webapp/admin/invite-candidates` | `services/tool-api/app/api/admin.py:1057` | Mini App: admin_invites | сессия Mini App + registration.code.issue | server.py, test_webapp_admin_pages.py, test_webapp_stand_invite.py | право проверяется в теле обработчика, а не в зависимости: registration.code.issue |
| POST `/webapp/admin/invite-revoke` | `services/tool-api/app/api/admin.py:1170` | Mini App: admin_invites | сессия Mini App + registration.code.issue | server.py, test_admin_archive_chat.py, test_admin_invites.py (+2) | право проверяется в теле обработчика, а не в зависимости: registration.code.issue |
| GET `/webapp/admin/invites` | `services/tool-api/app/api/admin.py:1050` | — | сессия Mini App + registration.code.issue | test_admin_archive_chat.py, test_admin_invites.py | право проверяется в теле обработчика, а не в зависимости: registration.code.issue; никто не зовёт |
| GET `/webapp/admin/notifications/settings` | `services/tool-api/app/api/admin.py:1557` | Mini App: admin_notifications | сессия Mini App + admin.panel | server.py, test_notification_defaults.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: admin.panel |
| POST `/webapp/admin/notifications/settings` | `services/tool-api/app/api/admin.py:1567` | Mini App: admin_notifications | сессия Mini App + admin.panel | server.py, test_notification_defaults.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: admin.panel |
| GET `/webapp/admin/overview` | `services/tool-api/app/api/admin.py:922` | Mini App: admin_archive, admin_core, admin_feedback, admin_invites, admin_rights, money_script | сессия Mini App | server.py, test_miniapp_archive_rights.py, test_miniapp_audit_rights.py (+9) | — |
| POST `/webapp/admin/person` | `services/tool-api/app/api/admin.py:1041` | Mini App: admin_people, admin_rights | сессия Mini App + participants.view | server.py, test_participants_card.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: participants.view |
| POST `/webapp/admin/person-new` | `services/tool-api/app/api/admin.py:1074` | Mini App: admin_invites | сессия Mini App + registration.code.issue | server.py, test_admin_new_person.py, test_cm01_login.py (+3) | право проверяется в теле обработчика, а не в зависимости: registration.code.issue |
| POST `/webapp/admin/reconcile` | `services/tool-api/app/api/admin.py:399` | Mini App: misc_reconcile | сессия Mini App + budget.import | server.py | право проверяется в теле обработчика, а не в зависимости: budget.import |
| GET `/webapp/admin/services` | `services/tool-api/app/api/admin.py:1305` | Mini App: services | сессия Mini App + admin.panel | server.py, test_cm08_miniapp.py, test_cm11_degradation.py | право проверяется в теле обработчика, а не в зависимости: admin.panel |
| POST `/webapp/admin/sheet-retry` | `services/tool-api/app/api/admin.py:1024` | — | сессия Mini App + budget.write | test_webapp_sheet_retry.py | право проверяется в теле обработчика, а не в зависимости: budget.write; никто не зовёт |
| GET `/webapp/admin/t/agent` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: intent_report, ui_review, ux_audit | — | server.py, test_agent_observability.py, test_miniapp_hub.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/archive` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_archive_rights.py, test_miniapp_hub.py (+3) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/audit` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_miniapp_audit_rights.py, test_miniapp_hub.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/budget` | `services/tool-api/app/pages/routes.py:237` | Mini App: routes (по шаблону) | — | server.py, test_miniapp_hub.py | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/dues` | `services/tool-api/app/pages/routes.py:232` | Mini App: admin_people, routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_ui_review.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/feedback` | `services/tool-api/app/pages/routes.py:232` | Mini App: home, routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_feedback_open_only.py, test_miniapp_feedback_rights.py (+4) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/invites` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_miniapp_invites_picker.py (+3) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/notifications` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_ui_review.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/people` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_js_behavior.py (+7) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/rights` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_miniapp_rights_screen.py (+2) | раздел из реестра app/webapp.py:SECTIONS |
| POST `/webapp/admin/tariff` | `services/tool-api/app/api/admin.py:1016` | Mini App: admin_dues, admin_people | сессия Mini App + budget.write | test_dues_admin_ui.py, test_webapp_admin_actions.py, test_webapp_admin_pages.py (+1) | право проверяется в теле обработчика, а не в зависимости: budget.write |
| POST `/webapp/admin/unarchive` | `services/tool-api/app/api/admin.py:1446` | Mini App: admin_archive | сессия Mini App + participants.manage | test_admin_archive_chat.py, test_admin_people_actions.py, test_miniapp_archive_rights.py (+1) | право проверяется в теле обработчика, а не в зависимости: participants.manage |
| GET `/webapp/assign` | `services/tool-api/app/pages/routes.py:273` | Mini App: misc_slots, routes; скрипты: ui_review | — | server.py, test_agent_task.py, test_gateway_agent_plan.py (+1) | — |
| POST `/webapp/assign` | `services/tool-api/app/api/events.py:971` | Mini App: misc_slots, routes; скрипты: ui_review | сессия Mini App | server.py, test_agent_task.py, test_gateway_agent_plan.py (+1) | — |
| POST `/webapp/auth` | `services/tool-api/app/api/webapp_api.py:35` | Mini App: webapp-shell.js; скрипты: issue_tokens | — | polls_helpers.py, server.py, test_admin_archive_chat.py (+42) | — |
| POST `/webapp/availability/clear` | `services/tool-api/app/api/schedule.py:1663` | Mini App: week_marks, week_sheet_load, week_template | сессия Mini App | server.py, test_webapp_availability_clear.py, test_webapp_grid.py (+1) | — |
| POST `/webapp/availability/sheet` | `services/tool-api/app/api/schedule.py:1287` | — | сессия Mini App | нет | никто не зовёт |
| POST `/webapp/availability/sheet/load` | `services/tool-api/app/api/schedule.py:1477` | Mini App: week_sheet_load | сессия Mini App | server.py, test_webapp_sheet_load.py | — |
| GET `/webapp/availability/sheet/preview` | `services/tool-api/app/api/schedule.py:1390` | Mini App: week_sheet_load | сессия Mini App | server.py, test_miniapp_sheet_load.py, test_webapp_sheet_load.py | — |
| GET `/webapp/availability/template` | `services/tool-api/app/api/schedule.py:1110` | — | сессия Mini App | test_gateway_quiet_parity.py | никто не зовёт |
| POST `/webapp/availability/template/apply` | `services/tool-api/app/api/schedule.py:1127` | Mini App: week_template | сессия Mini App | server.py, test_availability_week.py, test_webapp_week_page.py | — |
| POST `/webapp/availability/template/from-week` | `services/tool-api/app/api/schedule.py:1118` | Mini App: week_template | сессия Mini App | server.py, test_availability_week.py, test_webapp_week_page.py | — |
| GET `/webapp/availability/week` | `services/tool-api/app/api/schedule.py:1093` | Mini App: week_marks, week_sheet, week_sheet_load; чат: av_save | сессия Mini App | server.py, test_availability_week.py, test_gateway_quiet_parity.py (+4) | — |
| POST `/webapp/availability/week` | `services/tool-api/app/api/schedule.py:1102` | Mini App: week_marks, week_sheet, week_sheet_load; чат: av_save | сессия Mini App | server.py, test_availability_week.py, test_gateway_quiet_parity.py (+4) | — |
| POST `/webapp/boot` | `services/tool-api/app/api/webapp_api.py:100` | Mini App: layout, webapp-shell.js | — | test_miniapp_net_guard.py, test_webapp_boot.py | — |
| GET `/webapp/broadcast` | `services/tool-api/app/pages/routes.py:148` | Mini App: routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_broadcast_rights.py, test_miniapp_broadcast.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/categories` | `services/tool-api/app/api/budget.py:451` | Mini App: money_tx_script | сессия Mini App | server.py | — |
| GET `/webapp/days` | `services/tool-api/app/pages/routes.py:258` | Mini App: routes; чат: _buttons_with_week, send_menu; скрипты: ui_review | — | test_miniapp_system.py, test_static_contract.py, test_webapp_merge_deps.py (+1) | — |
| GET `/webapp/dues` | `services/tool-api/app/pages/routes.py:123` | Mini App: kit, routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_visual.py | экран Mini App: HTML без данных |
| GET `/webapp/feedback` | `services/tool-api/app/pages/routes.py:133` | Mini App: home, misc_feedback, routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_feedback.py, test_feedback_attachment.py (+3) | экран Mini App: HTML без данных |
| POST `/webapp/feedback` | `services/tool-api/app/api/admin.py:881` | Mini App: home, misc_feedback, routes; скрипты: build_screen_map, ui_review, ux_audit | сессия Mini App + feedback.send | server.py, test_feedback.py, test_feedback_attachment.py (+3) | право проверяется в теле обработчика, а не в зависимости: feedback.send |
| GET `/webapp/feedback/mine` | `services/tool-api/app/api/admin.py:899` | Mini App: misc_feedback | сессия Mini App | server.py, test_feedback.py, test_feedback_attachment.py (+2) | — |
| POST `/webapp/feedback/presign` | `services/tool-api/app/api/feedback.py:89` | Mini App: misc_feedback | сессия Mini App + feedback.send | server.py, test_feedback_attachment.py | право проверяется в теле обработчика, а не в зависимости: feedback.send |
| GET `/webapp/file` | `services/tool-api/app/pages/routes.py:178` | Mini App: admin_feedback, files_folder, files_grid, routes; скрипты: build_screen_map, main, open_file_page, pinch_probe_file, pinch_probe_screens, start_stand, ui_review | — | server.py, test_miniapp_files_download.py, test_miniapp_files_drive_card.py (+4) | экран Mini App: HTML без данных |
| GET `/webapp/files` | `services/tool-api/app/pages/routes.py:163` | Mini App: files_actions, files_file, files_folder, home, kit, routes; скрипты: build_screen_map, ui_review | — | server.py, test_agent_task.py, test_files_studio_roles.py (+13) | экран Mini App: HTML без данных |
| POST `/webapp/files/commit` | `services/tool-api/app/api/files.py:388` | Mini App: files_upload | сессия Mini App + files.access | server.py, test_files_delete.py, test_webapp_files.py (+2) | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/files/link` | `services/tool-api/app/api/files.py:438` | Mini App: files_link | сессия Mini App + files.access | server.py, test_files_delete.py, test_webapp_files_link_api.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/files/list` | `services/tool-api/app/api/files.py:335` | Mini App: files, files_tree | сессия Mini App + files.access | server.py, test_files_delete.py, test_files_studio_roles.py (+3) | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/files/presign` | `services/tool-api/app/api/files.py:348` | Mini App: files_upload | сессия Mini App + files.access | server.py, test_files_delete.py, test_webapp_files.py (+3) | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/files/search` | `services/tool-api/app/api/files.py:492` | Mini App: files, files_folder | сессия Mini App + files.access | server.py, test_files_delete.py, test_miniapp_files_search.py (+2) | право проверяется в теле обработчика, а не в зависимости: files.access |
| DELETE `/webapp/files/{file_id}` | `services/tool-api/app/api/files.py:523` | Mini App: files, files_folder, files_link, files_tree, files_upload | сессия Mini App + files.access | server.py, test_files_delete.py, test_files_studio_roles.py (+9) | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/files/{file_id}/access` | `services/tool-api/app/api/files.py:969` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py, test_files_studio_roles.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/files/{file_id}/access` | `services/tool-api/app/api/files.py:944` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py, test_files_studio_roles.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/files/{file_id}/access/revoke` | `services/tool-api/app/api/files.py:958` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/files/{file_id}/link` | `services/tool-api/app/api/files.py:857` | Mini App: files_file, files_folder | сессия Mini App + files.access | server.py, test_miniapp_shell_contract.py, test_webapp_files_link.py (+1) | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/files/{file_id}/meta` | `services/tool-api/app/api/files.py:463` | Mini App: files_file | сессия Mini App + files.access | server.py, test_miniapp_files_drive_card.py, test_webapp_files_meta.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/files/{file_id}/view` | `services/tool-api/app/api/files.py:791` | Mini App: admin_feedback, files_file | сессия Mini App + files.access | server.py, test_feedback_attachment.py, test_files_studio_roles.py (+2) | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/folder` | `services/tool-api/app/pages/routes.py:173` | Mini App: files_tree, routes; чат: _zip_refusal_text, cmd_zip; скрипты: build_screen_map, ui_review | — | server.py, test_miniapp_files_download.py, test_miniapp_files_folder_drive.py (+6) | экран Mini App: HTML без данных |
| GET `/webapp/folder/{folder_id}/files` | `services/tool-api/app/api/files.py:655` | Mini App: files_folder | сессия Mini App + files.access | server.py, test_folder_order.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/folder/{folder_id}/order` | `services/tool-api/app/api/files.py:677` | Mini App: files_folder | сессия Mini App + files.access | server.py, test_files_actions_screen.py, test_folder_order.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/folders` | `services/tool-api/app/api/files.py:781` | Mini App: files_actions, files_tree, files_upload | сессия Mini App + files.access | server.py, test_files_actions_screen.py, test_webapp_files_tree.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/folders` | `services/tool-api/app/api/files.py:909` | Mini App: files_actions, files_tree, files_upload | сессия Mini App + files.access | server.py, test_files_actions_screen.py, test_webapp_files_tree.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/folders/{folder_id}/delete` | `services/tool-api/app/api/files.py:933` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/folders/{folder_id}/rename` | `services/tool-api/app/api/files.py:921` | Mini App: files_tree | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/form-data` | `services/tool-api/app/api/webapp_api.py:151` | Mini App: admin_disciplines | сессия Mini App | server.py, test_master_role.py, test_webapp_search.py | — |
| GET `/webapp/grid` | `services/tool-api/app/api/schedule.py:58` | Mini App: week_summary | сессия Mini App + schedule.read.all | server.py, test_cm10_reports.py, test_miniapp_system.py (+3) | право проверяется в теле обработчика, а не в зависимости: schedule.read.all |
| GET `/webapp/home` | `services/tool-api/app/pages/routes.py:81` | Mini App: kit, layout, person, polls_mine, routes, webapp-shell.js; чат: _show_open_card, ensure_menu_button, main, with_home; скрипты: build_screen_map, label_of, ui_review, ux_audit | — | server.py, test_agent_task.py, test_miniapp_home_loading.py (+8) | экран Mini App: HTML без данных |
| GET `/webapp/home/data` | `services/tool-api/app/api/webapp_api.py:353` | Mini App: home, home_loading, home_tomorrow, money_script, money_tx, money_tx_rights, routes | сессия Mini App | server.py, test_broadcast_rights.py, test_cm08_miniapp.py (+7) | — |
| GET `/webapp/logo.png` | `services/tool-api/app/api/webapp_api.py:74` | Mini App: home_loading | — | server.py, test_webapp_assets.py | — |
| GET `/webapp/money` | `services/tool-api/app/pages/routes.py:106` | Mini App: layout, misc_dues, money_script, money_tx, money_tx_script, routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_cm08_miniapp.py, test_cm10_reports.py (+4) | экран Mini App: HTML без данных |
| GET `/webapp/month-grid` | `services/tool-api/app/api/schedule.py:120` | Mini App: month | сессия Mini App + schedule.read.all | server.py, test_webapp_month_grid.py, test_webapp_week_page.py | право проверяется в теле обработчика, а не в зависимости: schedule.read.all |
| GET `/webapp/more` | `services/tool-api/app/pages/routes.py:86` | Mini App: admin_core, files, home, home_loading, kit, layout, misc_broadcast, misc_feedback, misc_notifications, misc_profile, misc_reconcile, notes, polls, routes, services, webapp-shell.js; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_rights_loading.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/my` | `services/tool-api/app/api/people.py:318` | — | сессия Mini App | server.py, test_admin_people_actions.py, test_smoke_api_fixes.py (+1) | никто не зовёт |
| GET `/webapp/my/dues` | `services/tool-api/app/api/finance.py:17` | Mini App: misc_dues, money_script | сессия Mini App | server.py, test_dues_admin_ui.py, test_dues_tariff.py | — |
| GET `/webapp/my/sheet` | `services/tool-api/app/pages/routes.py:263` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_webapp_merge_deps.py | — |
| GET `/webapp/nav` | `services/tool-api/app/pages/routes.py:74` | Mini App: home_loading, layout, misc_reconcile, routes, webapp-shell.js, week_template | сессия Mini App | server.py, test_miniapp_net_guard.py, test_miniapp_reconcile_rights.py (+8) | — |
| GET `/webapp/notes` | `services/tool-api/app/pages/routes.py:168` | Mini App: routes; чат: cmd_notes, note_start, notes; скрипты: ui_review | — | server.py, test_agent_task.py, test_miniapp_notes.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/notes/list` | `services/tool-api/app/api/notes.py:296` | Mini App: notes | сессия Mini App + notes.write | server.py, test_notes_api.py | право проверяется в теле обработчика, а не в зависимости: notes.write |
| POST `/webapp/notes/save` | `services/tool-api/app/api/notes.py:305` | Mini App: notes | сессия Mini App + notes.write | server.py, test_cm08_miniapp.py, test_notes_api.py | право проверяется в теле обработчика, а не в зависимости: notes.write |
| DELETE `/webapp/notes/{note_id}` | `services/tool-api/app/api/notes.py:319` | Mini App: notes | сессия Mini App + notes.write | server.py, test_cm08_miniapp.py, test_notes_api.py | право проверяется в теле обработчика, а не в зависимости: notes.write |
| GET `/webapp/notifications` | `services/tool-api/app/pages/routes.py:128` | Mini App: admin_notifications, home, routes; чат: quiet_text; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_gateway_quiet.py, test_miniapp_hub.py | экран Mini App: HTML без данных |
| GET `/webapp/notifications/settings` | `services/tool-api/app/api/webapp_api.py:477` | Mini App: misc_notifications | сессия Mini App | server.py, test_gateway_quiet_parity.py, test_notification_defaults.py | — |
| POST `/webapp/notifications/settings` | `services/tool-api/app/api/webapp_api.py:483` | Mini App: misc_notifications | сессия Mini App | server.py, test_gateway_quiet_parity.py, test_notification_defaults.py | — |
| GET `/webapp/people` | `services/tool-api/app/pages/routes.py:268` | Mini App: routes; скрипты: ui_review | — | test_agent_task.py | — |
| GET `/webapp/person` | `services/tool-api/app/pages/routes.py:187` | Mini App: person, routes; чат: panel_person; скрипты: build_screen_map, ui_review | — | server.py, test_miniapp_js_behavior.py, test_miniapp_system.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/person/info` | `services/tool-api/app/api/people.py:643` | Mini App: person | сессия Mini App + participants.view | server.py, test_org_miniapp_screens.py | право проверяется в теле обработчика, а не в зависимости: participants.view |
| POST `/webapp/person/tariff` | `services/tool-api/app/api/people.py:669` | Mini App: person | сессия Mini App + budget.write | server.py, test_org_miniapp_screens.py | право проверяется в теле обработчика, а не в зависимости: budget.write |
| GET `/webapp/polls` | `services/tool-api/app/pages/routes.py:232` | Mini App: polls_edit, polls_results; скрипты: build_screen_map, polls_screens_review, ui_review | — | server.py, test_miniapp_polls.py, test_miniapp_polls_draft_copy.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/polls/answer` | `services/tool-api/app/pages/routes.py:218` | Mini App: polls_answer, routes; скрипты: build_screen_map, ui_review | — | server.py, test_miniapp_polls_answer_narrow.py, test_miniapp_polls_mine.py (+5) | экран Mini App: HTML без данных |
| POST `/webapp/polls/answer` | `services/tool-api/app/api/polls.py:834` | Mini App: polls_answer, routes; скрипты: build_screen_map, ui_review | сессия Mini App | server.py, test_miniapp_polls_answer_narrow.py, test_miniapp_polls_mine.py (+5) | — |
| POST `/webapp/polls/audience` | `services/tool-api/app/api/polls.py:422` | Mini App: polls_audience_js | сессия Mini App + no_poll_manage + poll.manage | polls_helpers.py, server.py | право проверяется в теле обработчика, а не в зависимости: no_poll_manage, poll.manage |
| GET `/webapp/polls/audience/data` | `services/tool-api/app/api/polls.py:393` | Mini App: polls_audience_js | сессия Mini App + no_poll_manage + poll.manage | server.py | право проверяется в теле обработчика, а не в зависимости: no_poll_manage, poll.manage |
| POST `/webapp/polls/close` | `services/tool-api/app/api/polls.py:550` | Mini App: polls_results | сессия Mini App + no_poll_manage + poll.manage | server.py, test_miniapp_polls_draft_copy.py, test_polls_delivery.py (+2) | право проверяется в теле обработчика, а не в зависимости: no_poll_manage, poll.manage |
| POST `/webapp/polls/copy` | `services/tool-api/app/api/polls.py:250` | Mini App: polls_results | сессия Mini App + no_poll_manage + poll.manage | server.py, test_miniapp_polls.py, test_miniapp_polls_draft_copy.py (+1) | право проверяется в теле обработчика, а не в зависимости: no_poll_manage, poll.manage |
| GET `/webapp/polls/edit` | `services/tool-api/app/pages/routes.py:194` | Mini App: polls, routes; скрипты: build_screen_map, polls_screens_review, ui_review | — | server.py, test_miniapp_polls.py, test_miniapp_polls_draft_copy.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/polls/edit/data` | `services/tool-api/app/api/polls.py:292` | Mini App: polls_edit_js | сессия Mini App + no_poll_manage + poll.manage (в теле — poll.results.all) | server.py, test_poll_interview_draft.py, test_polls_copy.py (+1) | проверка права в теле обработчика (через «*»); право проверяется в теле обработчика, а не в зависимости: no_poll_manage, poll.manage |
| GET `/webapp/polls/export` | `services/tool-api/app/api/polls.py:663` | — | — | server.py, test_miniapp_polls.py, test_polls_export_link.py (+3) | никто не зовёт |
| GET `/webapp/polls/export/link` | `services/tool-api/app/api/polls.py:643` | Mini App: polls_results | сессия Mini App | server.py, test_polls_export_link.py | — |
| GET `/webapp/polls/list` | `services/tool-api/app/api/polls.py:133` | Mini App: polls, polls_edit_js | сессия Mini App + no_poll_manage + poll.manage | server.py, test_poll_interview_draft.py, test_poll_interview_drafts.py (+3) | право проверяется в теле обработчика, а не в зависимости: no_poll_manage, poll.manage |
| GET `/webapp/polls/mine` | `services/tool-api/app/pages/routes.py:208` | Mini App: home, home_loading, polls_answer, routes; скрипты: build_screen_map, ui_review | — | server.py, test_miniapp_polls_mine.py, test_polls_webapp_home_marker.py | экран Mini App: HTML без данных |
| GET `/webapp/polls/mine/data` | `services/tool-api/app/api/polls.py:987` | Mini App: polls_mine, routes | сессия Mini App | server.py, test_polls_webapp.py | — |
| GET `/webapp/polls/results` | `services/tool-api/app/pages/routes.py:203` | Mini App: polls, polls_edit_js, routes; скрипты: build_screen_map, polls_screens_review, ui_review | — | server.py, test_miniapp_polls.py, test_miniapp_polls_draft_copy.py (+1) | экран Mini App: HTML без данных |
| GET `/webapp/polls/results/data` | `services/tool-api/app/api/polls.py:580` | Mini App: polls_results | сессия Mini App | server.py, test_polls_questionnaire_161.py, test_polls_results_caps.py (+1) | — |
| POST `/webapp/polls/save` | `services/tool-api/app/api/polls.py:207` | Mini App: polls_edit_js | сессия Mini App + no_poll_manage + poll.manage | polls_helpers.py, server.py, test_miniapp_polls.py (+3) | право проверяется в теле обработчика, а не в зависимости: no_poll_manage, poll.manage |
| POST `/webapp/polls/start` | `services/tool-api/app/api/polls.py:498` | Mini App: polls_edit_js | сессия Mini App + no_poll_manage + poll.manage | server.py, test_miniapp_polls.py, test_miniapp_polls_draft_copy.py (+10) | право проверяется в теле обработчика, а не в зависимости: no_poll_manage, poll.manage |
| GET `/webapp/polls/step` | `services/tool-api/app/api/polls.py:998` | Mini App: polls_answer | сессия Mini App | server.py, test_polls_webapp.py | — |
| POST `/webapp/preview` | `services/tool-api/app/api/events.py:964` | Mini App: misc_slots | сессия Mini App | server.py, test_miniapp_master_slots.py, test_miniapp_slots.py (+1) | — |
| GET `/webapp/profile` | `services/tool-api/app/api/people.py:364` | Mini App: misc_profile | сессия Mini App | server.py, test_master_role.py, test_webapp_profile.py | — |
| POST `/webapp/profile` | `services/tool-api/app/api/people.py:403` | Mini App: misc_profile | сессия Mini App | server.py, test_master_role.py, test_webapp_profile.py | — |
| GET `/webapp/reconcile` | `services/tool-api/app/pages/routes.py:143` | Mini App: layout, routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_miniapp_reconcile_rights.py, test_webapp_layout.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/schedule` | `services/tool-api/app/pages/routes.py:278` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_ui_review.py | — |
| GET `/webapp/search` | `services/tool-api/app/pages/routes.py:253` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_webapp_search.py | — |
| GET `/webapp/services` | `services/tool-api/app/pages/routes.py:158` | Mini App: routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_webapp_screen_rights.py, test_webapp_sections.py (+1) | экран Mini App: HTML без данных |
| GET `/webapp/shell.js` | `services/tool-api/app/api/webapp_api.py:125` | Mini App: layout; скрипты: pinch_probe_file, start_stand | — | server.py, test_routes_contract.py, test_static_contract.py (+4) | — |
| GET `/webapp/slots` | `services/tool-api/app/pages/routes.py:138` | Mini App: misc_slots_denied, misc_slots_discipline, routes, week_summary; чат: answer_slots, availability, run_menu_action; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_agent_task.py, test_gateway_slots_entry.py (+5) | экран Mini App: HTML без данных |
| POST `/webapp/slots` | `services/tool-api/app/api/schedule.py:1651` | Mini App: misc_slots_denied, misc_slots_discipline, routes, week_summary; чат: answer_slots, availability, run_menu_action; скрипты: build_screen_map, ui_review, ux_audit | сессия Mini App | server.py, test_agent_task.py, test_gateway_slots_entry.py (+5) | — |
| POST `/webapp/slots/find` | `services/tool-api/app/api/schedule.py:1071` | Mini App: misc_slots | сессия Mini App + schedule.slots | server.py, test_org_miniapp_screens.py | право проверяется в теле обработчика, а не в зависимости: schedule.slots |
| GET `/webapp/slots/people` | `services/tool-api/app/api/schedule.py:1036` | Mini App: misc_slots | сессия Mini App + schedule.slots | server.py, test_master_role.py, test_miniapp_master_slots.py (+1) | право проверяется в теле обработчика, а не в зависимости: schedule.slots |
| GET `/webapp/tags/pending` | `services/tool-api/app/api/files.py:985` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/tags/{tag_id}/approve` | `services/tool-api/app/api/files.py:994` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/tg.js` | `services/tool-api/app/api/webapp_api.py:60` | Mini App: layout | — | server.py, test_cm08_miniapp.py, test_webapp_assets.py (+1) | — |
| GET `/webapp/tx` | `services/tool-api/app/pages/routes.py:118` | Mini App: home_loading, money, money_script, routes; чат: run_menu_action; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_agent_task.py, test_miniapp_js_behavior.py (+2) | экран Mini App: HTML без данных |
| POST `/webapp/tx/save` | `services/tool-api/app/api/budget.py:480` | Mini App: money_tx_script, routes | сессия Mini App + budget.write | server.py, test_cm08_miniapp.py, test_org_miniapp_screens.py | право проверяется в теле обработчика, а не в зависимости: budget.write |
| GET `/webapp/ui` | `services/tool-api/app/pages/routes.py:153` | Mini App: kit, routes; скрипты: build, main, ui_review, ux_audit | — | server.py, test_miniapp_home_loading.py, test_miniapp_hub.py | экран Mini App: HTML без данных |
| GET `/webapp/ui.css` | `services/tool-api/app/api/webapp_api.py:138` | Mini App: layout, webapp-shell.js; скрипты: build | — | server.py, test_cm11_degradation.py, test_routes_contract.py (+3) | — |
| GET `/webapp/week` | `services/tool-api/app/pages/routes.py:91` | Mini App: home_loading, home_tomorrow, kit, layout, misc_slots, routes, week_sheet, week_summary; скрипты: build_screen_map, ui_review, ux_audit, week_no_mark_today | — | server.py, test_agent_task.py, test_cm08_miniapp.py (+16) | экран Mini App: HTML без данных |
| GET `/webapp/week/month` | `services/tool-api/app/pages/routes.py:101` | Mini App: routes, week_summary; скрипты: build_screen_map, ui_review | — | server.py, test_cm08_miniapp.py, test_cm10_reports.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/week/summary` | `services/tool-api/app/pages/routes.py:96` | Mini App: home, home_tomorrow, month, routes, week, week_sheet, week_summary; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_miniapp_home_loading.py, test_miniapp_month_grid.py (+5) | экран Mini App: HTML без данных |

## 3. Экраны Mini App и что они зовут

«Зовёт эндпоинты» — вызовы `api()`/`fetch()` внутри вёрстки экрана; навигационные ссылки сюда не входят (они видны в колонке «Интерфейсы» у самого маршрута). Экран собран из частей (`HOME_PAGE` = тело + скрипты, `misc.*` — реэкспорт), их содержимое раскрыто по ссылкам. `tests/webapp/server.py` — стенд браузерных тестов, а не тест.

| Экран | Точка входа | Код страницы | Зовёт эндпоинты | Тест |
|---|---|---|---|---|
| `/webapp` | `services/tool-api/app/pages/routes.py:113` | `misc.PROFILE_PAGE` | `/webapp/more`, `/webapp/profile` | нет |
| `/webapp/admin/t/agent` | `services/tool-api/app/pages/routes.py:232` | `admin.AGENT_PAGE` | `/webapp/admin/agent` | нет |
| `/webapp/admin/t/archive` | `services/tool-api/app/pages/routes.py:232` | `admin.ARCHIVE_PAGE` | `/webapp/admin/unarchive` | нет |
| `/webapp/admin/t/audit` | `services/tool-api/app/pages/routes.py:232` | `admin.AUDIT_PAGE` | — | нет |
| `/webapp/admin/t/dues` | `services/tool-api/app/pages/routes.py:232` | `admin.DUES_ADMIN_PAGE` | `/webapp/admin/dues/accrue`, `/webapp/admin/tariff` | нет |
| `/webapp/admin/t/feedback` | `services/tool-api/app/pages/routes.py:232` | `admin.FEEDBACK_QUEUE_PAGE` | `/webapp/admin/feedback`, `/webapp/admin/feedback/decide`, `/webapp/admin/overview`, `/webapp/files/{}/view` | нет |
| `/webapp/admin/t/invites` | `services/tool-api/app/pages/routes.py:232` | `admin.INVITES_PAGE` | `/webapp/admin/invite`, `/webapp/admin/invite-candidates`, `/webapp/admin/invite-revoke`, `/webapp/admin/person-new` | нет |
| `/webapp/admin/t/notifications` | `services/tool-api/app/pages/routes.py:232` | `admin.NOTIFICATION_DEFAULTS_PAGE` | `/webapp/admin/notifications/settings` | нет |
| `/webapp/admin/t/people` | `services/tool-api/app/pages/routes.py:232` | `admin.PEOPLE_PAGE` | `/webapp/admin/archive`, `/webapp/admin/block`, `/webapp/admin/discipline`, `/webapp/admin/grant`, `/webapp/admin/person`, `/webapp/admin/tariff`, `/webapp/form-data` | нет |
| `/webapp/admin/t/rights` | `services/tool-api/app/pages/routes.py:232` | `admin.RIGHTS_PAGE` | `/webapp/admin/grant`, `/webapp/admin/person` | нет |
| `/webapp/broadcast` | `services/tool-api/app/pages/routes.py:148` | `misc.BROADCAST_PAGE` | `/webapp/admin/broadcast`, `/webapp/admin/broadcast/preview`, `/webapp/more` | нет |
| `/webapp/dues` | `services/tool-api/app/pages/routes.py:123` | `misc.DUES_PAGE` | `/webapp/money`, `/webapp/my/dues` | нет |
| `/webapp/feedback` | `services/tool-api/app/pages/routes.py:133` | `misc.FEEDBACK_PAGE` | `/webapp/feedback`, `/webapp/feedback/mine`, `/webapp/feedback/presign`, `/webapp/more` | нет |
| `/webapp/file` | `services/tool-api/app/pages/routes.py:178` | `files.FILE_PAGE` | `/webapp/admin/overview`, `/webapp/files`, `/webapp/files/{}/link`, `/webapp/files/{}/meta`, `/webapp/files/{}/view` | нет |
| `/webapp/files` | `services/tool-api/app/pages/routes.py:163` | `files.FILES_PAGE` | `/webapp/admin/overview`, `/webapp/files/commit`, `/webapp/files/link`, `/webapp/files/presign`, `/webapp/files/{}/access`, `/webapp/files/{}/access/revoke`, `/webapp/folders`, `/webapp/folders/{}/delete`, `/webapp/folders/{}/rename`, `/webapp/more`, `/webapp/tags/pending`, `/webapp/tags/{}/approve` | нет |
| `/webapp/folder` | `services/tool-api/app/pages/routes.py:173` | `files.FOLDER_PAGE` | `/webapp/admin/overview`, `/webapp/files`, `/webapp/files/search`, `/webapp/files/{}/link`, `/webapp/folder/{}/files`, `/webapp/folder/{}/order` | нет |
| `/webapp/home` | `services/tool-api/app/pages/routes.py:81` | `home.HOME_PAGE` | `/webapp/home/data` | нет |
| `/webapp/money` | `services/tool-api/app/pages/routes.py:106` | `money.MONEY_PAGE` | `/webapp/admin/overview`, `/webapp/home/data`, `/webapp/my/dues` | нет |
| `/webapp/more` | `services/tool-api/app/pages/routes.py:86` | `home.MORE_PAGE` | `/webapp/home/data` | нет |
| `/webapp/notes` | `services/tool-api/app/pages/routes.py:168` | `notes.NOTES_PAGE` | `/webapp/admin/overview`, `/webapp/more`, `/webapp/notes/list`, `/webapp/notes/save` | нет |
| `/webapp/notifications` | `services/tool-api/app/pages/routes.py:128` | `misc.NOTIFICATIONS_PAGE` | `/webapp/more`, `/webapp/notifications/settings` | нет |
| `/webapp/person` | `services/tool-api/app/pages/routes.py:187` | `person.PERSON_PAGE` | `/webapp/home`, `/webapp/person/info`, `/webapp/person/tariff` | нет |
| `/webapp/polls` | `services/tool-api/app/pages/routes.py:232` | `admin.POLLS_PAGE` | `/webapp/more`, `/webapp/polls/list` | нет |
| `/webapp/polls/answer` | `services/tool-api/app/pages/routes.py:218` | `polls_answer.POLLS_ANSWER_PAGE` | `/webapp/polls/answer`, `/webapp/polls/mine`, `/webapp/polls/step` | нет |
| `/webapp/polls/edit` | `services/tool-api/app/pages/routes.py:194` | `polls_edit.POLL_EDIT_PAGE` | `/webapp/polls`, `/webapp/polls/audience`, `/webapp/polls/audience/data`, `/webapp/polls/edit/data`, `/webapp/polls/list`, `/webapp/polls/save`, `/webapp/polls/start` | нет |
| `/webapp/polls/mine` | `services/tool-api/app/pages/routes.py:208` | `polls_mine.POLLS_MINE_PAGE` | `/webapp/home`, `/webapp/polls/mine/data` | нет |
| `/webapp/polls/results` | `services/tool-api/app/pages/routes.py:203` | `polls_results.POLL_RESULTS_PAGE` | `/webapp/polls`, `/webapp/polls/close`, `/webapp/polls/copy`, `/webapp/polls/export/link`, `/webapp/polls/results/data` | нет |
| `/webapp/reconcile` | `services/tool-api/app/pages/routes.py:143` | `misc.RECONCILE_PAGE` | `/webapp/admin/reconcile`, `/webapp/more`, `/webapp/nav` | нет |
| `/webapp/services` | `services/tool-api/app/pages/routes.py:158` | `services.SERVICES_PAGE` | `/webapp/admin/services`, `/webapp/more` | нет |
| `/webapp/slots` | `services/tool-api/app/pages/routes.py:138` | `misc.SLOTS_PAGE` | `/webapp/assign`, `/webapp/preview`, `/webapp/slots/find`, `/webapp/slots/people`, `/webapp/week` | нет |
| `/webapp/tx` | `services/tool-api/app/pages/routes.py:118` | `money.TX_PAGE` | `/webapp/categories`, `/webapp/home/data`, `/webapp/money`, `/webapp/tx/save` | нет |
| `/webapp/ui` | `services/tool-api/app/pages/routes.py:153` | `kit.UI_KIT_PAGE` | — | нет |
| `/webapp/week` | `services/tool-api/app/pages/routes.py:91` | `week.WEEK_PAGE` | `/webapp/availability/clear`, `/webapp/availability/sheet/load`, `/webapp/availability/sheet/preview`, `/webapp/availability/template/apply`, `/webapp/availability/template/from-week`, `/webapp/availability/week` | нет |
| `/webapp/week/month` | `services/tool-api/app/pages/routes.py:101` | `week.MONTH_PAGE` | `/webapp/month-grid`, `/webapp/week/summary` | нет |
| `/webapp/week/summary` | `services/tool-api/app/pages/routes.py:96` | `week.SUMMARY_PAGE` | `/webapp/grid` | нет |

## 4. Telegram-гейтвей: команды, кнопки, текстовые ветки

Право в чате — то, что проверяет сам обработчик (`core.has(caps, …)`, `in caps`). «—» значит, что собственной проверки нет: окончательное решение принимает tool-api по праву эндпоинта из колонки «Зовёт эндпоинты». Вызовы через общие помощники (`send_invite`, `panel_person` и подобные) видны в строке помощника, а не вызывающего.

| Действие | Триггер | Точка входа | Право в чате | Тест | Зовёт эндпоинты |
|---|---|---|---|---|---|
| кнопка `panel_archive` | F.data == 'panel:archive' | `services/tg-gateway/app/routers/admin.py:138` | — | test_gateway_chat_parity.py | — |
| кнопка `panel_person_archive` | F.data.startswith('panel:archive:') | `services/tg-gateway/app/routers/admin.py:147` | — | test_gateway_chat_parity.py | `/admin/archive` |
| кнопка `panel_person_unarchive` | F.data.startswith('panel:unarchive:') | `services/tg-gateway/app/routers/admin.py:171` | — | test_gateway_chat_parity.py | `/admin/unarchive` |
| кнопка `panel_person_block` | F.data.startswith('panel:block:') | `services/tg-gateway/app/routers/admin.py:224` | — | test_gateway_chat_parity.py | — |
| кнопка `panel_person_unblock` | F.data.startswith('panel:unblock:') | `services/tg-gateway/app/routers/admin.py:230` | — | test_gateway_chat_parity.py | — |
| кнопка `panel_person` | F.data.startswith('panel:person:') | `services/tg-gateway/app/routers/admin.py:236` | admin.panel, budget.write, participants.manage | test_gateway_chat_parity.py | `/tools/participants/{}/card`, `/webapp/person` |
| кнопка `panel_person_roles` | F.data.startswith('panel:proles:') | `services/tg-gateway/app/routers/admin.py:278` | — | test_gateway_chat_parity.py, test_gateway_routing.py | `/tools/participants/{}/card` |
| кнопка `panel_person_role_toggle` | F.data.startswith('panel:role:') | `services/tg-gateway/app/routers/admin.py:307` | — | нет | `/admin/grant` |
| кнопка `panel_person_master` | F.data.startswith('panel:mroles:') | `services/tg-gateway/app/routers/admin.py:338` | — | test_gateway_chat_parity.py | `/tools/disciplines` |
| кнопка `panel_person_master_link` | F.data.startswith('panel:mrel:') | `services/tg-gateway/app/routers/admin.py:377` | — | test_gateway_chat_parity.py | `/admin/participant/discipline` |
| кнопка `panel_person_master_grant` | F.data.startswith('panel:mgrant:') | `services/tg-gateway/app/routers/admin.py:401` | — | test_gateway_chat_parity.py | `/admin/grant` |
| кнопка `panel_person_caps` | F.data.startswith('panel:caps:') | `services/tg-gateway/app/routers/admin.py:430` | — | test_gateway_routing.py | `/admin/capabilities`, `/tools/participants/{}/card` |
| кнопка `panel_person_cap_toggle` | F.data.startswith('panel:cap:') | `services/tg-gateway/app/routers/admin.py:459` | — | нет | `/admin/grant` |
| кнопка `panel_person_tariff` | F.data.startswith('panel:tariff:') | `services/tg-gateway/app/routers/admin.py:477` | — | test_gateway_chat_parity.py, test_gateway_routing.py | `/tools/participants/{}/card` |
| сообщение `panel_tariff_save` | PanelEdit.tariff, F.text | `services/tg-gateway/app/routers/admin.py:505` | — | нет | `/tools/participants/{}/dues-tariff` |
| кнопка `panel_rights` | F.data == 'panel:rights' | `services/tg-gateway/app/routers/admin.py:529` | — | test_gateway_chat_parity.py, test_gateway_routing.py | `/admin/capabilities`, `/admin/grant` |
| кнопка `panel_money` | F.data.startswith('panel:money') | `services/tg-gateway/app/routers/admin.py:552` | budget.write | test_panel_money_periods.py | `/tools/budget/recent` |
| кнопка `reconcile_apply` | F.data == 'panel:reconcile_apply' | `services/tg-gateway/app/routers/admin.py:593` | — | нет | `/admin/reconcile` |
| кнопка `panel_sheet_retry` | F.data == 'panel:sheet_retry' | `services/tg-gateway/app/routers/admin.py:602` | — | test_gateway_routing.py, test_gateway_sheet_retry.py | `/tools/budget/sheet-retry` |
| кнопка `panel_feedback` | F.data == 'panel:feedback' | `services/tg-gateway/app/routers/admin.py:613` | — | test_admin_panel_wiring.py, test_panel_money_periods.py | `/tools/feedback` |
| кнопка `panel_back` | F.data == 'panel:root' | `services/tg-gateway/app/routers/admin.py:62` | — | test_gateway_chat_parity.py, test_panel_money_periods.py | — |
| кнопка `panel_audit` | F.data == 'panel:audit' | `services/tg-gateway/app/routers/admin.py:656` | — | нет | `/tools/audit/recent` |
| кнопка `panel_health` | F.data == 'panel:health' | `services/tg-gateway/app/routers/admin.py:678` | — | нет | `/health` |
| кнопка `panel_people` | F.data == 'panel:people' | `services/tg-gateway/app/routers/admin.py:68` | — | test_gateway_chat_parity.py, test_gateway_menu_rights.py (+1) | `/tools/participants` |
| кнопка `panel_reconcile` | F.data == 'panel:reconcile' | `services/tg-gateway/app/routers/admin.py:691` | — | test_gateway_registration.py | `/admin/reconcile` |
| кнопка `reconcile_apply_panel` | F.data == 'rec:apply' | `services/tg-gateway/app/routers/admin.py:704` | — | test_gateway_registration.py | `/admin/reconcile` |
| кнопка `agent_action_cb` | F.data.startswith('ag:') | `services/tg-gateway/app/routers/agent.py:23` | — | нет | `/agent/actions/{}/{}` |
| сообщение `assign_start` | F.text.regexp(ASSIGN_Q_PATTERN, flags=re.IGNORECASE) | `services/tg-gateway/app/routers/assign.py:173` | — | нет | — |
| кнопка `draft_cancel` | F.data == 'evdraft:no' | `services/tg-gateway/app/routers/assign.py:210` | — | test_gateway_assign_dialog.py | — |
| кнопка `assign_pick` | F.data.startswith('evpick:') | `services/tg-gateway/app/routers/assign.py:218` | — | нет | `/tools/events/choose` |
| кнопка `assign_confirm` | F.data.startswith('evok:') | `services/tg-gateway/app/routers/assign.py:238` | — | test_event_chat_card.py | `/tools/events/assign` |
| кнопка `assign_cancel` | F.data.startswith('evno:') | `services/tg-gateway/app/routers/assign.py:267` | — | нет | — |
| кнопка `slots_more` | F.data.startswith('slots:') | `services/tg-gateway/app/routers/availability.py:147` | — | нет | — |
| кнопка `av_day` | F.data.startswith('av:day:') | `services/tg-gateway/app/routers/availability.py:257` | — | test_gateway_avail_parity.py, test_poll_keyboard.py | — |
| сообщение `av_day_text` | AvailForm.day, F.text | `services/tg-gateway/app/routers/availability.py:274` | — | нет | — |
| кнопка `av_status` | F.data.startswith('av:s:') | `services/tg-gateway/app/routers/availability.py:300` | — | test_gateway_avail_parity.py | — |
| кнопка `av_time` | F.data.startswith('av:t:') | `services/tg-gateway/app/routers/availability.py:319` | — | нет | — |
| сообщение `av_time_text` | AvailForm.time, F.text | `services/tg-gateway/app/routers/availability.py:337` | — | нет | `/tools/availability/parse-interval` |
| сообщение `av_note_text` | AvailForm.note, F.text | `services/tg-gateway/app/routers/availability.py:361` | — | нет | — |
| кнопка `av_note_skip` | F.data == 'av:n:skip' | `services/tg-gateway/app/routers/availability.py:371` | — | нет | — |
| кнопка `av_save` | F.data == 'av:ok' | `services/tg-gateway/app/routers/availability.py:378` | — | test_gateway_avail_parity.py, test_gateway_avail_text.py | `/tools/availability/commit`, `/tools/availability/week`, `/webapp/availability/week` |
| кнопка `av_cancel` | F.data == 'av:no' | `services/tg-gateway/app/routers/availability.py:442` | — | test_gateway_avail_text.py | — |
| кнопка `av_fix` | F.data == 'av:fix' | `services/tg-gateway/app/routers/availability.py:449` | — | нет | — |
| сообщение `cmd_busy` | Command('busy') | `services/tg-gateway/app/routers/availability.py:511` | schedule.availability.self | нет | — |
| сообщение `cmd_week` | lambda message: parsers.command_name(message.text) in WEEK_COMMANDS | `services/tg-gateway/app/routers/availability.py:553` | schedule.availability.self | нет | — |
| кнопка `week_action` | F.data.startswith('week:') | `services/tg-gateway/app/routers/availability.py:565` | schedule.availability.self | test_gateway_avail_parity.py, test_ui_texts.py | `/tools/availability/template/apply`, `/tools/availability/template/from-week` |
| кнопка `budget_type` | F.data.startswith('bud:type:') | `services/tg-gateway/app/routers/budget.py:103` | — | нет | — |
| кнопка `budget_cancel` | F.data == 'bud:no' | `services/tg-gateway/app/routers/budget.py:126` | — | нет | — |
| кнопка `budget_fix` | F.data == 'bud:fix' | `services/tg-gateway/app/routers/budget.py:133` | — | нет | — |
| кнопка `form_start` | F.data.startswith('form:') | `services/tg-gateway/app/routers/budget.py:141` | — | button_scan.py, test_agent_features.py (+1) | — |
| сообщение `form_amount` | BudgetForm.amount, F.text | `services/tg-gateway/app/routers/budget.py:160` | — | нет | `/tools/categories` |
| кнопка `form_category` | F.data.startswith('cat:') | `services/tg-gateway/app/routers/budget.py:188` | — | нет | — |
| сообщение `form_category_text` | BudgetForm.category, F.text | `services/tg-gateway/app/routers/budget.py:211` | — | нет | — |
| кнопка `form_save` | F.data == 'bud:form_ok' | `services/tg-gateway/app/routers/budget.py:226` | — | нет | `/tools/budget/commit` |
| сообщение `cmd_budget_input` | Command('expense', 'income') | `services/tg-gateway/app/routers/budget.py:249` | budget.write | нет | — |
| кнопка `budget_confirm` | F.data == 'bud:ok' | `services/tg-gateway/app/routers/budget.py:42` | — | button_scan.py, test_gateway_assign_dialog.py (+2) | `/tools/budget/commit` |
| кнопка `intent_category` | F.data.startswith('icat:') | `services/tg-gateway/app/routers/budget.py:76` | — | нет | — |
| кнопка `agent_confirm_cb` | F.data.startswith('agc:') | `services/tg-gateway/app/routers/confirm.py:36` | — | test_gateway_agent_confirm.py | — |
| кнопка `agent_plan_cb` | F.data.startswith('agp:') | `services/tg-gateway/app/routers/confirm.py:69` | — | test_gateway_agent_plan.py | — |
| кнопка `event_cancel_ask` | F.data.startswith('evcancel:') | `services/tg-gateway/app/routers/events.py:114` | — | test_event_chat_card.py | — |
| кнопка `event_move_ask` | F.data.startswith('evmove:') | `services/tg-gateway/app/routers/events.py:172` | — | test_event_chat_card.py | — |
| сообщение `event_move_text` | EventMove.await_time, F.text | `services/tg-gateway/app/routers/events.py:205` | — | нет | — |
| кнопка `event_card` | F.data.startswith('evcard:') | `services/tg-gateway/app/routers/events.py:98` | — | test_event_chat_card.py | — |
| ошибка `on_handler_error` | — | `services/tg-gateway/app/routers/fallback.py:18` | — | нет | — |
| сообщение `state_fallback_text` | StateFilter('*'), F.text & ~F.text.startswith('/') | `services/tg-gateway/app/routers/fallback.py:39` | — | нет | — |
| кнопка `feedback_decide` | F.data.startswith('fbd:') | `services/tg-gateway/app/routers/feedback.py:109` | — | нет | `/tools/feedback/decide` |
| кнопка `fb_kind` | F.data.startswith('fb:') | `services/tg-gateway/app/routers/feedback.py:19` | — | нет | — |
| сообщение `fb_text` | Feedback.await_text, F.text, ~F.text.startswith('/') | `services/tg-gateway/app/routers/feedback.py:31` | — | нет | — |
| сообщение `feedback_triage` | Command('feedback') | `services/tg-gateway/app/routers/feedback.py:63` | — | нет | `/tools/feedback` |
| кнопка `fb_confirm` | F.data.startswith('fbq:') | `services/tg-gateway/app/routers/feedback.py:85` | — | нет | — |
| сообщение `cmd_rm` | Command('rm', 'удалить') | `services/tg-gateway/app/routers/files.py:155` | — | нет | — |
| кнопка `file_delete_ask` | F.data.startswith('fdel:') | `services/tg-gateway/app/routers/files.py:168` | admin.panel | test_gateway_files_delete.py | — |
| кнопка `file_delete_confirm` | F.data.startswith('fdel-ok:') | `services/tg-gateway/app/routers/files.py:188` | admin.panel | test_gateway_files_delete.py | `/files/{}` |
| кнопка `file_delete_cancel` | F.data.startswith('fdel-no') | `services/tg-gateway/app/routers/files.py:208` | — | test_gateway_files_delete.py | — |
| сообщение `cmd_files` | Command('files', texts.TITLE_FILES) | `services/tg-gateway/app/routers/files.py:256` | — | test_action_registry.py, test_files_delete.py (+3) | `/files` |
| кнопка `file_send` | F.data.startswith('fl:') | `services/tg-gateway/app/routers/files.py:278` | admin.panel | test_gateway_files_rights.py | `/files/{}`, `/files/{}/content`, `/files/{}/link` |
| сообщение `cmd_folders` | Command('folders', 'папки') | `services/tg-gateway/app/routers/files.py:329` | — | test_action_registry.py, test_files_actions_screen.py (+5) | `/folders` |
| сообщение `cmd_mkdir` | Command('mkdir', 'папка') | `services/tg-gateway/app/routers/files.py:356` | — | test_gateway_files_rights.py | `/folders` |
| сообщение `cmd_rename` | Command('rename', 'переименовать') | `services/tg-gateway/app/routers/files.py:399` | — | test_gateway_files_rights.py | `/folders/{}/rename` |
| сообщение `cmd_rmfolder` | Command('rmfolder', 'удалить-папку') | `services/tg-gateway/app/routers/files.py:418` | — | test_gateway_files_rights.py | `/folders/{}/delete` |
| сообщение `cmd_order` | Command('order', 'порядок') | `services/tg-gateway/app/routers/files.py:438` | — | test_gateway_files_rights.py | `/folders/{}/order` |
| сообщение `cmd_access` | Command('access', 'доступ') | `services/tg-gateway/app/routers/files.py:464` | — | test_gateway_files_rights.py | `/admin/files/{}/access` |
| сообщение `cmd_revoke` | Command('revoke', 'отозвать') | `services/tg-gateway/app/routers/files.py:492` | — | test_gateway_files_rights.py | `/files/{}/access/revoke` |
| сообщение `cmd_zip` | Command('zip', 'архив') | `services/tg-gateway/app/routers/files.py:537` | — | test_gateway_files_rights.py, test_gateway_files_zip.py (+1) | `/folders/{}/zip`, `/webapp/folder` |
| сообщение `cmd_search` | Command('search', 'найти') | `services/tg-gateway/app/routers/files.py:576` | — | test_gateway_files_rights.py | `/files` |
| сообщение `cmd_tags` | Command('tags', 'теги') | `services/tg-gateway/app/routers/files.py:604` | — | test_action_registry.py, test_files_actions_screen.py (+2) | `/tags` |
| сообщение `cmd_tag` | Command('tag', 'тег') | `services/tg-gateway/app/routers/files.py:623` | — | test_gateway_files_rights.py | `/files/{}/tags`, `/tags` |
| сообщение `cmd_tag_ok` | Command('tag-ok', 'тег-ок') | `services/tg-gateway/app/routers/files.py:666` | — | test_gateway_files_rights.py | `/tags/{}/approve` |
| сообщение `on_video` | F.video \| F.video_note \| F.animation | `services/tg-gateway/app/routers/files.py:80` | — | нет | — |
| сообщение `on_file` | F.document \| F.photo \| F.audio \| F.voice | `services/tg-gateway/app/routers/files.py:90` | — | нет | — |
| сообщение `cmd_intro` | Command('intro') | `services/tg-gateway/app/routers/intro.py:133` | — | test_intro.py | — |
| кнопка `intro_save` | F.data == 'intro:save' | `services/tg-gateway/app/routers/intro.py:138` | — | нет | `/tools/participants/me`, `/tools/profile/me` |
| кнопка `intro_cancel` | F.data == 'intro:cancel' | `services/tg-gateway/app/routers/intro.py:168` | — | нет | `/tools/participants/me` |
| сообщение `intro_name` | Intro.name, F.text | `services/tg-gateway/app/routers/intro.py:186` | — | нет | — |
| сообщение `intro_alias` | Intro.alias, F.text | `services/tg-gateway/app/routers/intro.py:191` | — | нет | — |
| сообщение `intro_phone` | Intro.phone, F.text | `services/tg-gateway/app/routers/intro.py:196` | — | нет | — |
| сообщение `intro_birthday` | Intro.birthday, F.text | `services/tg-gateway/app/routers/intro.py:201` | — | нет | — |
| сообщение `intro_travel` | Intro.travel, F.text | `services/tg-gateway/app/routers/intro.py:206` | — | нет | — |
| сообщение `intro_plays` | Intro.plays, F.text | `services/tg-gateway/app/routers/intro.py:211` | — | нет | — |
| сообщение `intro_roles` | Intro.roles, F.text | `services/tg-gateway/app/routers/intro.py:216` | — | нет | — |
| сообщение `intro_trainings` | Intro.trainings, F.text | `services/tg-gateway/app/routers/intro.py:221` | — | нет | — |
| сообщение `intro_manifest` | Intro.manifest, F.text | `services/tg-gateway/app/routers/intro.py:226` | — | нет | — |
| кнопка `intro_skip` | F.data == 'intro:skip' | `services/tg-gateway/app/routers/intro.py:231` | — | нет | — |
| сообщение `start` | CommandStart() | `services/tg-gateway/app/routers/menu.py:145` | — | нет | `/admin/bootstrap`, `/tools/participants/me` |
| сообщение `on_menu_button` | StateFilter(None, AssignDraft.await_kind, AssignDraft.await_date, AssignDraft.await_time), F.text.in_(keyboards.MENU_SURFACE) | `services/tg-gateway/app/routers/menu.py:375` | — | нет | — |
| кнопка `menu_item_cb` | F.data.startswith('mi:') | `services/tg-gateway/app/routers/menu.py:382` | — | test_agent_features.py, test_gateway_menu_rights.py | — |
| сообщение `on_dialog_control` | F.text.in_(keyboards.DIALOG_CONTROLS) | `services/tg-gateway/app/routers/menu.py:623` | — | нет | — |
| сообщение `task_frame_text` | TaskFrame.await_text, F.text, ~F.text.startswith('/') | `services/tg-gateway/app/routers/menu.py:628` | — | нет | — |
| сообщение `on_text` | StateFilter(None, Reg.await_code, AssignDraft.await_kind, AssignDraft.await_date, AssignDraft.await_time), F.text & ~F.text.startswith('/') | `services/tg-gateway/app/routers/menu.py:668` | budget.write | нет | `/tools/assistant/ask`, `/tools/participants/check_code` |
| кнопка `reg_discipline` | Reg.await_discipline, F.data.startswith('reg:disc:') | `services/tg-gateway/app/routers/menu.py:833` | — | test_onboarding_sequence.py | — |
| сообщение `cmd_help` | Command('help') | `services/tg-gateway/app/routers/menu.py:852` | — | test_gateway_help_memo.py | — |
| сообщение `menu_cmd` | Command('menu') | `services/tg-gateway/app/routers/menu.py:862` | — | test_gateway_assign_dialog.py | — |
| сообщение `cmd_quiet` | lambda message: parsers.command_name(message.text) in QUIET_COMMANDS | `services/tg-gateway/app/routers/menu.py:930` | — | нет | — |
| кнопка `quiet_action` | F.data.startswith('quiet:') | `services/tg-gateway/app/routers/menu.py:959` | — | test_gateway_quiet.py | — |
| сообщение `cmd_notes` | Command('заметки', 'notes') | `services/tg-gateway/app/routers/notes.py:104` | — | нет | `/webapp/notes` |
| кнопка `note_confirm` | F.data.startswith('note:') | `services/tg-gateway/app/routers/notes.py:112` | notes.write | test_gateway_notes.py | `/tools/notes` |
| сообщение `cmd_note` | Command('заметка', 'note') | `services/tg-gateway/app/routers/notes.py:98` | — | test_gateway_command_hints.py, test_gateway_notes.py | — |
| сообщение `poll_free_text` | PollAnswer.await_text, F.text | `services/tg-gateway/app/routers/polls.py:114` | — | нет | `/tools/polls/answer` |
| кнопка `poll_step` | F.data.startswith('p:') | `services/tg-gateway/app/routers/polls.py:76` | — | нет | `/tools/polls/step` |
| кнопка `my_dues` | F.data == 'sar:dues' | `services/tg-gateway/app/routers/profile.py:114` | — | нет | `/tools/dues/me` |
| сообщение `cmd_dues` | Command('dues') | `services/tg-gateway/app/routers/profile.py:55` | — | нет | `/tools/dues/me` |
| сообщение `profile_cmd` | Command('profile') | `services/tg-gateway/app/routers/profile.py:64` | — | нет | `/tools/profile/me`, `/webapp` |

Ветки ответа на свободный текст (`app/routing.py`, порядок применения):

| Правило | Роль | Что ловит | Точка входа |
|---|---|---|---|
| `about_me` | вопрос | ^(кто я\|кто я такой\|кто я такая\|мой профиль\|мои роли\|моя роль\|кто я в боте) | `services/tg-gateway/app/routing.py:80` |
| `list_people` | вопрос | (список.*(участник\|людей\|пользовател)\|кто\s+(зарегистр\|в\s+системе\|у\s+нас\s+в\s+боте)\|зарегистрирован\|состав\s+(студи\|т | `services/tg-gateway/app/routing.py:81` |
| `capabilities` | вопрос | (что\s+(ты\s+\|вы\s+)?(вообще\s+)?(умеешь\|умеете\|можешь\|можете)\b\|что\s+(бот\|ассистент)\s+(вообще\s+)?(умеет\|может)\b\|что | `services/tg-gateway/app/routing.py:84` |
| `howto` | вопрос | ^(как\|где\|чем\|почему\|зачем\|можно\s+ли\|подскажи\|объясни\|научи)\b | `services/tg-gateway/app/routing.py:85` |
| `slots` | вопрос | (окн[оа]\b\|когда\s+(все\s+)?свободн\|найди\s+(время\|окно)\|общее\s+время\|когда\s+можно\s+собрат\|свободные\s+часы) | `services/tg-gateway/app/routing.py:86` |
| `money` | вопрос | (сколько\s+(у\s+нас\s+)?(денег\|средств\|на\s+счет\|осталось)\|какой\s+баланс\|баланс\s+студи\|сколько\s+потратили\|сколько\s+з | `services/tg-gateway/app/routing.py:87` |
| `schedule` | вопрос | (расписан\|что\s+у\s+меня\s+(сегодня\|завтра\|послезавтра\|на\s+неделе)\|что\s+(у\s+нас\s+)?(на\|в)\s+(неделе\|пн\|вт\|ср\|чт\|пт\|с | `services/tg-gateway/app/routing.py:88` |
| `availability_q` | вопрос | (кто\s+((сегодня\|завтра\|послезавтра\|сейчас)\s+)?(занят\|свободен)\|покажи\s+занятост\|занятость\s+на\s+(сегодня\|завтра)) | `services/tg-gateway/app/routing.py:89` |
| `availability` | ввод данных | занят\|свобод\|могу\|не могу\|не смогу\|удобн\|работаю | `services/tg-gateway/app/routing.py:90` |
| `assign` | ввод данных | (назначь\|назначить\|запланируй\|поставь\s+(репетици\|показ\|тренинг\|аренд\|встреч)) | `services/tg-gateway/app/routing.py:92` |
| `budget` | ввод данных | расход\|потрат\|оплат\|заплат\|купил\|отдал\|приход\|поступ\|взнос\|перевел | `services/tg-gateway/app/routing.py:93` |

## 5. Инструменты агента

| Инструмент | Право | Только чтение | Обработчик | Тест | Что делает |
|---|---|---|---|---|---|
| `now` | любому активному | да | `services/tool-api/app/agently/tools.py:68` | test_agent_ask.py, test_agent_eval.py (+4) | Текущая дата, время и день недели по Самаре |
| `read_schedule` | любому активному | да | `services/tool-api/app/agently/tools.py:81` | test_agent_tools.py | Ближайшие события: репетиции, показы, занятия |
| `read_availability` | любому активному | да | `services/tool-api/app/agently/tools.py:108` | test_agent_task.py, test_agent_tools.py | Занятость людей по часам: кто свободен и когда |
| `read_budget` | budget.read | да | `services/tool-api/app/agently/tools.py:140` | server.py, test_agent_ask.py (+4) | Бюджет студии за месяц: сколько денег пришло (доход), сколько потратили (расход), баланс и последние операции |
| `read_dues` | budget.read.self, budget.read | да | `services/tool-api/app/agently/tools.py:171` | test_agent_observability.py, test_agent_tools.py | Взносы: свои начисления, тариф, оплата и долг; с правом budget |
| `list_files` | любому активному | да | `services/tool-api/app/agently/file_tools.py:50` | test_agent_files.py, test_agent_single_loop.py (+1) | Список своих файлов: имя, размер, папка; поиск по слову в имени, тегах и тексте |
| `read_file` | любому активному | да | `services/tool-api/app/agently/file_tools.py:75` | test_agent_files.py, test_agent_single_loop.py (+1) | Текст файла по номеру из list_files: имя и цитата |
| `find_slots` | schedule.slots | да | `services/tool-api/app/agently/tools.py:258` | test_agent_slots_tool.py, test_agent_tools.py | Окна, когда нужные люди свободны: репетиция, тренинг, показ, встреча, аренда зала |
| `propose_event` | schedule.write | да | `services/tool-api/app/agently/tools.py:316` | test_agent_task.py, test_agent_tools.py (+1) | Предложить событие: тип, люди, дата, время, зал |
| `propose_note` | notes.write | да | `services/tool-api/app/agently/tools.py:396` | test_agent_task.py, test_agent_tools.py (+1) | Предложить заметку: личную или общую |
| `propose_move` | любому активному | да | `services/tool-api/app/agently/file_tools.py:105` | test_agent_files.py, test_agent_single_loop.py (+1) | Предложить переложить свой файл в папку |
| `propose_budget_tx` | budget.write | да | `services/tool-api/app/agently/tools.py:427` | test_agent_ask.py, test_agent_features.py (+3) | Предложить записать расход или доход |
| `web_search` | любому активному | да | `services/tool-api/app/agently/tools.py:208` | test_agent_eval.py, test_agent_tools.py | Внешний поиск в интернете |

## 6. Действия без права

Проверки на входе нет: ни возможности, ни сессии Mini App, ни сервисного токена. Это не всегда дефект: экраны Mini App — публичные константы (данные проверяются в API), SDK и CSS отдаются всем, регистрация и вход по определению идут до сессии. Дефект — когда без права меняются данные.

| Действие | Точка входа | Интерфейсы | Право | Тест | Примечание |
|---|---|---|---|---|---|
| GET `/health` | `services/tool-api/app/main.py:245` | чат: panel_health, today_local; скрипты: _declared_routes, check_availability, check_health, check_services, fetch_health, health, health_agent, install_launchd, neighbor_counts, restore_dump, start_services (по шаблону) | — | test_arch_drift.py, test_ci_images.py, test_cm01_login.py (+2) | — |
| POST `/tools/participants/attach` | `services/tool-api/app/api/people.py:589` | чат: _attach_by_code | — | test_admin_new_person.py, test_cm01_login.py, test_gateway_registration.py (+4) | — |
| POST `/tools/participants/check_code` | `services/tool-api/app/api/people.py:527` | чат: on_text | — | test_admin_invites.py, test_admin_new_person.py, test_gateway_registration.py (+3) | — |
| GET `/webapp` | `services/tool-api/app/pages/routes.py:113` | Mini App: home, routes; чат: profile_cmd, run_menu_action; скрипты: build, build_screen_map, chat_entries, label_of, neighbor_counts, restore_dump, ui_review, ux_audit (по шаблону) | — | server.py, test_agent_features.py, test_miniapp_master_disciplines.py (+5) | экран Mini App: HTML без данных |
| GET `/webapp/admin` | `services/tool-api/app/pages/routes.py:225` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_static_contract.py, test_webapp_admin.py | — |
| GET `/webapp/admin/t/agent` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: intent_report, ui_review, ux_audit | — | server.py, test_agent_observability.py, test_miniapp_hub.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/archive` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_archive_rights.py, test_miniapp_hub.py (+3) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/audit` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_miniapp_audit_rights.py, test_miniapp_hub.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/budget` | `services/tool-api/app/pages/routes.py:237` | Mini App: routes (по шаблону) | — | server.py, test_miniapp_hub.py | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/dues` | `services/tool-api/app/pages/routes.py:232` | Mini App: admin_people, routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_ui_review.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/feedback` | `services/tool-api/app/pages/routes.py:232` | Mini App: home, routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_feedback_open_only.py, test_miniapp_feedback_rights.py (+4) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/invites` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_miniapp_invites_picker.py (+3) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/notifications` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_ui_review.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/people` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_js_behavior.py (+7) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/rights` | `services/tool-api/app/pages/routes.py:232` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_miniapp_rights_screen.py (+2) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/assign` | `services/tool-api/app/pages/routes.py:273` | Mini App: misc_slots, routes; скрипты: ui_review | — | server.py, test_agent_task.py, test_gateway_agent_plan.py (+1) | — |
| POST `/webapp/auth` | `services/tool-api/app/api/webapp_api.py:35` | Mini App: webapp-shell.js; скрипты: issue_tokens | — | polls_helpers.py, server.py, test_admin_archive_chat.py (+42) | — |
| POST `/webapp/boot` | `services/tool-api/app/api/webapp_api.py:100` | Mini App: layout, webapp-shell.js | — | test_miniapp_net_guard.py, test_webapp_boot.py | — |
| GET `/webapp/broadcast` | `services/tool-api/app/pages/routes.py:148` | Mini App: routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_broadcast_rights.py, test_miniapp_broadcast.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/days` | `services/tool-api/app/pages/routes.py:258` | Mini App: routes; чат: _buttons_with_week, send_menu; скрипты: ui_review | — | test_miniapp_system.py, test_static_contract.py, test_webapp_merge_deps.py (+1) | — |
| GET `/webapp/dues` | `services/tool-api/app/pages/routes.py:123` | Mini App: kit, routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_visual.py | экран Mini App: HTML без данных |
| GET `/webapp/feedback` | `services/tool-api/app/pages/routes.py:133` | Mini App: home, misc_feedback, routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_feedback.py, test_feedback_attachment.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/file` | `services/tool-api/app/pages/routes.py:178` | Mini App: admin_feedback, files_folder, files_grid, routes; скрипты: build_screen_map, main, open_file_page, pinch_probe_file, pinch_probe_screens, start_stand, ui_review | — | server.py, test_miniapp_files_download.py, test_miniapp_files_drive_card.py (+4) | экран Mini App: HTML без данных |
| GET `/webapp/files` | `services/tool-api/app/pages/routes.py:163` | Mini App: files_actions, files_file, files_folder, home, kit, routes; скрипты: build_screen_map, ui_review | — | server.py, test_agent_task.py, test_files_studio_roles.py (+13) | экран Mini App: HTML без данных |
| GET `/webapp/folder` | `services/tool-api/app/pages/routes.py:173` | Mini App: files_tree, routes; чат: _zip_refusal_text, cmd_zip; скрипты: build_screen_map, ui_review | — | server.py, test_miniapp_files_download.py, test_miniapp_files_folder_drive.py (+6) | экран Mini App: HTML без данных |
| GET `/webapp/home` | `services/tool-api/app/pages/routes.py:81` | Mini App: kit, layout, person, polls_mine, routes, webapp-shell.js; чат: _show_open_card, ensure_menu_button, main, with_home; скрипты: build_screen_map, label_of, ui_review, ux_audit | — | server.py, test_agent_task.py, test_miniapp_home_loading.py (+8) | экран Mini App: HTML без данных |
| GET `/webapp/logo.png` | `services/tool-api/app/api/webapp_api.py:74` | Mini App: home_loading | — | server.py, test_webapp_assets.py | — |
| GET `/webapp/money` | `services/tool-api/app/pages/routes.py:106` | Mini App: layout, misc_dues, money_script, money_tx, money_tx_script, routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_cm08_miniapp.py, test_cm10_reports.py (+4) | экран Mini App: HTML без данных |
| GET `/webapp/more` | `services/tool-api/app/pages/routes.py:86` | Mini App: admin_core, files, home, home_loading, kit, layout, misc_broadcast, misc_feedback, misc_notifications, misc_profile, misc_reconcile, notes, polls, routes, services, webapp-shell.js; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_rights_loading.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/my/sheet` | `services/tool-api/app/pages/routes.py:263` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_webapp_merge_deps.py | — |
| GET `/webapp/notes` | `services/tool-api/app/pages/routes.py:168` | Mini App: routes; чат: cmd_notes, note_start, notes; скрипты: ui_review | — | server.py, test_agent_task.py, test_miniapp_notes.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/notifications` | `services/tool-api/app/pages/routes.py:128` | Mini App: admin_notifications, home, routes; чат: quiet_text; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_gateway_quiet.py, test_miniapp_hub.py | экран Mini App: HTML без данных |
| GET `/webapp/people` | `services/tool-api/app/pages/routes.py:268` | Mini App: routes; скрипты: ui_review | — | test_agent_task.py | — |
| GET `/webapp/person` | `services/tool-api/app/pages/routes.py:187` | Mini App: person, routes; чат: panel_person; скрипты: build_screen_map, ui_review | — | server.py, test_miniapp_js_behavior.py, test_miniapp_system.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/polls` | `services/tool-api/app/pages/routes.py:232` | Mini App: polls_edit, polls_results; скрипты: build_screen_map, polls_screens_review, ui_review | — | server.py, test_miniapp_polls.py, test_miniapp_polls_draft_copy.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/polls/answer` | `services/tool-api/app/pages/routes.py:218` | Mini App: polls_answer, routes; скрипты: build_screen_map, ui_review | — | server.py, test_miniapp_polls_answer_narrow.py, test_miniapp_polls_mine.py (+5) | экран Mini App: HTML без данных |
| GET `/webapp/polls/edit` | `services/tool-api/app/pages/routes.py:194` | Mini App: polls, routes; скрипты: build_screen_map, polls_screens_review, ui_review | — | server.py, test_miniapp_polls.py, test_miniapp_polls_draft_copy.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/polls/export` | `services/tool-api/app/api/polls.py:663` | — | — | server.py, test_miniapp_polls.py, test_polls_export_link.py (+3) | никто не зовёт |
| GET `/webapp/polls/mine` | `services/tool-api/app/pages/routes.py:208` | Mini App: home, home_loading, polls_answer, routes; скрипты: build_screen_map, ui_review | — | server.py, test_miniapp_polls_mine.py, test_polls_webapp_home_marker.py | экран Mini App: HTML без данных |
| GET `/webapp/polls/results` | `services/tool-api/app/pages/routes.py:203` | Mini App: polls, polls_edit_js, routes; скрипты: build_screen_map, polls_screens_review, ui_review | — | server.py, test_miniapp_polls.py, test_miniapp_polls_draft_copy.py (+1) | экран Mini App: HTML без данных |
| GET `/webapp/reconcile` | `services/tool-api/app/pages/routes.py:143` | Mini App: layout, routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_miniapp_reconcile_rights.py, test_webapp_layout.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/schedule` | `services/tool-api/app/pages/routes.py:278` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_ui_review.py | — |
| GET `/webapp/search` | `services/tool-api/app/pages/routes.py:253` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_webapp_search.py | — |
| GET `/webapp/services` | `services/tool-api/app/pages/routes.py:158` | Mini App: routes; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_webapp_screen_rights.py, test_webapp_sections.py (+1) | экран Mini App: HTML без данных |
| GET `/webapp/shell.js` | `services/tool-api/app/api/webapp_api.py:125` | Mini App: layout; скрипты: pinch_probe_file, start_stand | — | server.py, test_routes_contract.py, test_static_contract.py (+4) | — |
| GET `/webapp/slots` | `services/tool-api/app/pages/routes.py:138` | Mini App: misc_slots_denied, misc_slots_discipline, routes, week_summary; чат: answer_slots, availability, run_menu_action; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_agent_task.py, test_gateway_slots_entry.py (+5) | экран Mini App: HTML без данных |
| GET `/webapp/tg.js` | `services/tool-api/app/api/webapp_api.py:60` | Mini App: layout | — | server.py, test_cm08_miniapp.py, test_webapp_assets.py (+1) | — |
| GET `/webapp/tx` | `services/tool-api/app/pages/routes.py:118` | Mini App: home_loading, money, money_script, routes; чат: run_menu_action; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_agent_task.py, test_miniapp_js_behavior.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/ui` | `services/tool-api/app/pages/routes.py:153` | Mini App: kit, routes; скрипты: build, main, ui_review, ux_audit | — | server.py, test_miniapp_home_loading.py, test_miniapp_hub.py | экран Mini App: HTML без данных |
| GET `/webapp/ui.css` | `services/tool-api/app/api/webapp_api.py:138` | Mini App: layout, webapp-shell.js; скрипты: build | — | server.py, test_cm11_degradation.py, test_routes_contract.py (+3) | — |
| GET `/webapp/week` | `services/tool-api/app/pages/routes.py:91` | Mini App: home_loading, home_tomorrow, kit, layout, misc_slots, routes, week_sheet, week_summary; скрипты: build_screen_map, ui_review, ux_audit, week_no_mark_today | — | server.py, test_agent_task.py, test_cm08_miniapp.py (+16) | экран Mini App: HTML без данных |
| GET `/webapp/week/month` | `services/tool-api/app/pages/routes.py:101` | Mini App: routes, week_summary; скрипты: build_screen_map, ui_review | — | server.py, test_cm08_miniapp.py, test_cm10_reports.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/week/summary` | `services/tool-api/app/pages/routes.py:96` | Mini App: home, home_tomorrow, month, routes, week, week_sheet, week_summary; скрипты: build_screen_map, ui_review, ux_audit | — | server.py, test_miniapp_home_loading.py, test_miniapp_month_grid.py (+5) | экран Mini App: HTML без данных |

## 7. Права, проверенные в теле обработчика

Три пути проверки права — не считая зависимости `core.require(…)` в §2: `core._webapp_require(who, "…")` у Mini App (7a), служебный Bearer-токен на входе (7b) и ручная проверка `"*" in caps` (7c).

### 7a. `core._webapp_require(who, "…")` — Mini App

Обработчик получает участника зависимостью `core.webapp_actor` (сессия), а возможность проверяет сам: `_webapp_require` — это `require()` для прямого вызова (`app/core.py:720-733`). В колонке «Право» §2 такая возможность приписана к сессии.

| Действие | Точка входа | Право | Сессия | Тест |
|---|---|---|---|---|
| GET `/webapp/admin/agent` | `services/tool-api/app/api/admin.py:1354` | admin.panel | сессия Mini App | server.py, test_agent_observability.py |
| POST `/webapp/admin/archive` | `services/tool-api/app/api/admin.py:1394` | participants.manage | сессия Mini App | test_admin_archive_chat.py, test_admin_people_actions.py, test_miniapp_archive_rights.py (+2) |
| POST `/webapp/admin/block` | `services/tool-api/app/api/admin.py:1522` | participants.manage | сессия Mini App | test_admin_people_actions.py, test_webapp_pages_js.py |
| POST `/webapp/admin/broadcast` | `services/tool-api/app/api/admin.py:1616` | notify.send | сессия Mini App | server.py, test_broadcast_rights.py, test_cm05_notifications.py |
| GET `/webapp/admin/broadcast/preview` | `services/tool-api/app/api/admin.py:1598` | notify.send | сессия Mini App | server.py, test_broadcast_rights.py, test_cm05_notifications.py (+1) |
| POST `/webapp/admin/discipline` | `services/tool-api/app/api/admin.py:1006` | participants.manage | сессия Mini App | server.py, test_master_role.py |
| GET `/webapp/admin/dues` | `services/tool-api/app/api/finance.py:110` | budget.write | сессия Mini App | server.py, test_dues_admin_ui.py, test_webapp_admin_pages.py |
| POST `/webapp/admin/dues/accrue` | `services/tool-api/app/api/finance.py:174` | budget.write | сессия Mini App | server.py, test_dues_admin_ui.py, test_webapp_admin_pages.py |
| GET `/webapp/admin/feedback` | `services/tool-api/app/api/admin.py:1177` | feedback.triage | сессия Mini App | server.py, test_feedback_attachment.py |
| POST `/webapp/admin/feedback/decide` | `services/tool-api/app/api/admin.py:1189` | feedback.triage | сессия Mini App | test_feedback_open_only.py, test_webapp_merge_deps.py |
| POST `/webapp/admin/grant` | `services/tool-api/app/api/admin.py:997` | participants.manage | сессия Mini App | server.py, test_master_role.py, test_miniapp_master_grant.py (+3) |
| POST `/webapp/admin/invite` | `services/tool-api/app/api/admin.py:1122` | registration.code.issue | сессия Mini App | server.py, test_admin_archive_chat.py, test_admin_invites.py (+9) |
| GET `/webapp/admin/invite-candidates` | `services/tool-api/app/api/admin.py:1057` | registration.code.issue | сессия Mini App | server.py, test_webapp_admin_pages.py, test_webapp_stand_invite.py |
| POST `/webapp/admin/invite-revoke` | `services/tool-api/app/api/admin.py:1170` | registration.code.issue | сессия Mini App | server.py, test_admin_archive_chat.py, test_admin_invites.py (+2) |
| GET `/webapp/admin/invites` | `services/tool-api/app/api/admin.py:1050` | registration.code.issue | сессия Mini App | test_admin_archive_chat.py, test_admin_invites.py |
| GET `/webapp/admin/notifications/settings` | `services/tool-api/app/api/admin.py:1557` | admin.panel | сессия Mini App | server.py, test_notification_defaults.py, test_webapp_admin_pages.py |
| POST `/webapp/admin/notifications/settings` | `services/tool-api/app/api/admin.py:1567` | admin.panel | сессия Mini App | server.py, test_notification_defaults.py, test_webapp_admin_pages.py |
| POST `/webapp/admin/person` | `services/tool-api/app/api/admin.py:1041` | participants.view | сессия Mini App | server.py, test_participants_card.py, test_webapp_admin_pages.py |
| POST `/webapp/admin/person-new` | `services/tool-api/app/api/admin.py:1074` | registration.code.issue | сессия Mini App | server.py, test_admin_new_person.py, test_cm01_login.py (+3) |
| POST `/webapp/admin/reconcile` | `services/tool-api/app/api/admin.py:399` | budget.import | сессия Mini App | server.py |
| GET `/webapp/admin/services` | `services/tool-api/app/api/admin.py:1305` | admin.panel | сессия Mini App | server.py, test_cm08_miniapp.py, test_cm11_degradation.py |
| POST `/webapp/admin/sheet-retry` | `services/tool-api/app/api/admin.py:1024` | budget.write | сессия Mini App | test_webapp_sheet_retry.py |
| POST `/webapp/admin/tariff` | `services/tool-api/app/api/admin.py:1016` | budget.write | сессия Mini App | test_dues_admin_ui.py, test_webapp_admin_actions.py, test_webapp_admin_pages.py (+1) |
| POST `/webapp/admin/unarchive` | `services/tool-api/app/api/admin.py:1446` | participants.manage | сессия Mini App | test_admin_archive_chat.py, test_admin_people_actions.py, test_miniapp_archive_rights.py (+1) |
| POST `/webapp/feedback` | `services/tool-api/app/api/admin.py:881` | feedback.send | сессия Mini App | server.py, test_feedback.py, test_feedback_attachment.py (+3) |
| POST `/webapp/feedback/presign` | `services/tool-api/app/api/feedback.py:89` | feedback.send | сессия Mini App | server.py, test_feedback_attachment.py |
| POST `/webapp/files/commit` | `services/tool-api/app/api/files.py:388` | files.access | сессия Mini App | server.py, test_files_delete.py, test_webapp_files.py (+2) |
| POST `/webapp/files/link` | `services/tool-api/app/api/files.py:438` | files.access | сессия Mini App | server.py, test_files_delete.py, test_webapp_files_link_api.py |
| GET `/webapp/files/list` | `services/tool-api/app/api/files.py:335` | files.access | сессия Mini App | server.py, test_files_delete.py, test_files_studio_roles.py (+3) |
| POST `/webapp/files/presign` | `services/tool-api/app/api/files.py:348` | files.access | сессия Mini App | server.py, test_files_delete.py, test_webapp_files.py (+3) |
| GET `/webapp/files/search` | `services/tool-api/app/api/files.py:492` | files.access | сессия Mini App | server.py, test_files_delete.py, test_miniapp_files_search.py (+2) |
| DELETE `/webapp/files/{file_id}` | `services/tool-api/app/api/files.py:523` | files.access | сессия Mini App | server.py, test_files_delete.py, test_files_studio_roles.py (+9) |
| GET `/webapp/files/{file_id}/access` | `services/tool-api/app/api/files.py:969` | files.access | сессия Mini App | server.py, test_files_actions_screen.py, test_files_studio_roles.py |
| POST `/webapp/files/{file_id}/access` | `services/tool-api/app/api/files.py:944` | files.access | сессия Mini App | server.py, test_files_actions_screen.py, test_files_studio_roles.py |
| POST `/webapp/files/{file_id}/access/revoke` | `services/tool-api/app/api/files.py:958` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| GET `/webapp/files/{file_id}/link` | `services/tool-api/app/api/files.py:857` | files.access | сессия Mini App | server.py, test_miniapp_shell_contract.py, test_webapp_files_link.py (+1) |
| POST `/webapp/files/{file_id}/meta` | `services/tool-api/app/api/files.py:463` | files.access | сессия Mini App | server.py, test_miniapp_files_drive_card.py, test_webapp_files_meta.py |
| GET `/webapp/files/{file_id}/view` | `services/tool-api/app/api/files.py:791` | files.access | сессия Mini App | server.py, test_feedback_attachment.py, test_files_studio_roles.py (+2) |
| GET `/webapp/folder/{folder_id}/files` | `services/tool-api/app/api/files.py:655` | files.access | сессия Mini App | server.py, test_folder_order.py |
| POST `/webapp/folder/{folder_id}/order` | `services/tool-api/app/api/files.py:677` | files.access | сессия Mini App | server.py, test_files_actions_screen.py, test_folder_order.py |
| GET `/webapp/folders` | `services/tool-api/app/api/files.py:781` | files.access | сессия Mini App | server.py, test_files_actions_screen.py, test_webapp_files_tree.py |
| POST `/webapp/folders` | `services/tool-api/app/api/files.py:909` | files.access | сессия Mini App | server.py, test_files_actions_screen.py, test_webapp_files_tree.py |
| POST `/webapp/folders/{folder_id}/delete` | `services/tool-api/app/api/files.py:933` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| POST `/webapp/folders/{folder_id}/rename` | `services/tool-api/app/api/files.py:921` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| GET `/webapp/grid` | `services/tool-api/app/api/schedule.py:58` | schedule.read.all | сессия Mini App | server.py, test_cm10_reports.py, test_miniapp_system.py (+3) |
| GET `/webapp/month-grid` | `services/tool-api/app/api/schedule.py:120` | schedule.read.all | сессия Mini App | server.py, test_webapp_month_grid.py, test_webapp_week_page.py |
| GET `/webapp/notes/list` | `services/tool-api/app/api/notes.py:296` | notes.write | сессия Mini App | server.py, test_notes_api.py |
| POST `/webapp/notes/save` | `services/tool-api/app/api/notes.py:305` | notes.write | сессия Mini App | server.py, test_cm08_miniapp.py, test_notes_api.py |
| DELETE `/webapp/notes/{note_id}` | `services/tool-api/app/api/notes.py:319` | notes.write | сессия Mini App | server.py, test_cm08_miniapp.py, test_notes_api.py |
| GET `/webapp/person/info` | `services/tool-api/app/api/people.py:643` | participants.view | сессия Mini App | server.py, test_org_miniapp_screens.py |
| POST `/webapp/person/tariff` | `services/tool-api/app/api/people.py:669` | budget.write | сессия Mini App | server.py, test_org_miniapp_screens.py |
| POST `/webapp/polls/audience` | `services/tool-api/app/api/polls.py:422` | no_poll_manage, poll.manage | сессия Mini App | polls_helpers.py, server.py |
| GET `/webapp/polls/audience/data` | `services/tool-api/app/api/polls.py:393` | no_poll_manage, poll.manage | сессия Mini App | server.py |
| POST `/webapp/polls/close` | `services/tool-api/app/api/polls.py:550` | no_poll_manage, poll.manage | сессия Mini App | server.py, test_miniapp_polls_draft_copy.py, test_polls_delivery.py (+2) |
| POST `/webapp/polls/copy` | `services/tool-api/app/api/polls.py:250` | no_poll_manage, poll.manage | сессия Mini App | server.py, test_miniapp_polls.py, test_miniapp_polls_draft_copy.py (+1) |
| GET `/webapp/polls/edit/data` | `services/tool-api/app/api/polls.py:292` | no_poll_manage, poll.manage | сессия Mini App | server.py, test_poll_interview_draft.py, test_polls_copy.py (+1) |
| GET `/webapp/polls/list` | `services/tool-api/app/api/polls.py:133` | no_poll_manage, poll.manage | сессия Mini App | server.py, test_poll_interview_draft.py, test_poll_interview_drafts.py (+3) |
| POST `/webapp/polls/save` | `services/tool-api/app/api/polls.py:207` | no_poll_manage, poll.manage | сессия Mini App | polls_helpers.py, server.py, test_miniapp_polls.py (+3) |
| POST `/webapp/polls/start` | `services/tool-api/app/api/polls.py:498` | no_poll_manage, poll.manage | сессия Mini App | server.py, test_miniapp_polls.py, test_miniapp_polls_draft_copy.py (+10) |
| POST `/webapp/slots/find` | `services/tool-api/app/api/schedule.py:1071` | schedule.slots | сессия Mini App | server.py, test_org_miniapp_screens.py |
| GET `/webapp/slots/people` | `services/tool-api/app/api/schedule.py:1036` | schedule.slots | сессия Mini App | server.py, test_master_role.py, test_miniapp_master_slots.py (+1) |
| GET `/webapp/tags/pending` | `services/tool-api/app/api/files.py:985` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| POST `/webapp/tags/{tag_id}/approve` | `services/tool-api/app/api/files.py:994` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| POST `/webapp/tx/save` | `services/tool-api/app/api/budget.py:480` | budget.write | сессия Mini App | server.py, test_cm08_miniapp.py, test_org_miniapp_screens.py |

### 7b. Служебный Bearer-токен

Маршрут зовёт другой сервис, а не человек: в теле сверяется `Authorization: Bearer {TOOL_API_TOKEN}` (`hmac.compare_digest`). У `POST /admin/bootstrap` дополнительно сверяется `ADMIN_CHAT_ID`.

| Действие | Точка входа | Тест |
|---|---|---|
| POST `/admin/bootstrap` | `services/tool-api/app/api/admin.py:105` | нет |
| POST `/admin/files/cold-rotate` | `services/tool-api/app/api/admin.py:1694` | test_cold.py, test_service_routes_auth.py |
| GET `/admin/files/storage` | `services/tool-api/app/api/admin.py:1668` | test_cold.py, test_service_routes_auth.py |

### 7c. Ручная проверка `"*" in caps`

Возможность проверяется не `core.require(...)`, а руками в теле обработчика: `"*" in caps or "<право>" in caps`. Одно правило — два пути проверки.

| Действие | Точка входа | Право | В теле проверяет |
|---|---|---|---|
| POST `/tools/availability/reconcile` | `services/tool-api/app/api/schedule.py:1533` | schedule.availability.self | schedule.read.all |
| GET `/tools/availability/week` | `services/tool-api/app/api/schedule.py:742` | schedule.availability.self | schedule.read.all |
| GET `/webapp/polls/edit/data` | `services/tool-api/app/api/polls.py:292` | сессия Mini App + no_poll_manage + poll.manage | poll.results.all |

## 8. Эндпоинты, которые не зовёт ни один интерфейс

Кандидаты на удаление: ни экран, ни бот, ни скрипт не обращаются по этому пути. Тест упомянут отдельно — если он есть, путь живёт ради теста.

| Действие | Точка входа | Право | Тест |
|---|---|---|---|
| POST `/admin/files/cold-rotate` | `services/tool-api/app/api/admin.py:1694` | сервисный токен | test_cold.py, test_service_routes_auth.py |
| GET `/admin/files/storage` | `services/tool-api/app/api/admin.py:1668` | сервисный токен | test_cold.py, test_service_routes_auth.py |
| GET `/files/{file_id}/access` | `services/tool-api/app/api/files.py:291` | любой активный | test_file_access.py, test_files_studio_roles.py |
| POST `/files/{file_id}/folder` | `services/tool-api/app/api/files.py:594` | любой активный | test_files_actions_screen.py, test_folder_order.py, test_webapp_files_link.py (+1) |
| GET `/folders/{folder_id}/files` | `services/tool-api/app/api/files.py:566` | любой активный | test_files_actions_screen.py, test_files_delete.py, test_folder_order.py |
| POST `/tools/agent/files` | `services/tool-api/app/api/admin.py:1726` | любой активный | нет |
| POST `/tools/availability/reconcile` | `services/tool-api/app/api/schedule.py:1533` | schedule.availability.self | test_availability_sheet.py |
| POST `/tools/availability/sheet` | `services/tool-api/app/api/schedule.py:1279` | schedule.availability.self | test_availability_sheet.py |
| GET `/tools/availability/sheet/mismatches` | `services/tool-api/app/api/schedule.py:1490` | schedule.availability.self | test_availability_sheet.py |
| POST `/tools/availability/test-sheet` | `services/tool-api/app/api/schedule.py:1306` | admin.panel | test_google_test_sheet_api.py |
| GET `/tools/calendar/health` | `services/tool-api/app/api/events.py:902` | любой активный | test_calendar_api.py |
| POST `/tools/dues/accrue` | `services/tool-api/app/api/finance.py:65` | budget.write | test_cm04_money.py, test_dues_accrue.py |
| GET `/tools/feedback/mine` | `services/tool-api/app/api/admin.py:290` | любой активный | test_cm06_feedback.py |
| GET `/tools/feedback/open` | `services/tool-api/app/api/feedback.py:72` | feedback.triage | test_feedback_triage.py |
| GET `/tools/finance/granularity` | `services/tool-api/app/api/finance.py:190` | budget.read | нет |
| POST `/tools/finance/granularity` | `services/tool-api/app/api/finance.py:200` | budget.import | нет |
| GET `/tools/finance/sources` | `services/tool-api/app/api/finance.py:224` | budget.read | нет |
| POST `/tools/finance/sources` | `services/tool-api/app/api/finance.py:241` | budget.import | нет |
| DELETE `/tools/finance/sources/{source_id}` | `services/tool-api/app/api/finance.py:260` | budget.import | нет |
| GET `/tools/google/freshness` | `services/tool-api/app/api/google.py:73` | schedule.read.all | test_google_mode_api.py, test_google_no_pii.py |
| POST `/tools/google/mode` | `services/tool-api/app/api/google.py:65` | admin.panel | test_google_mode_api.py |
| POST `/tools/google/refresh` | `services/tool-api/app/api/google.py:86` | schedule.read.all | test_google_no_pii.py |
| GET `/tools/intent/categories` | `services/tool-api/app/api/admin.py:718` | любой активный | test_intent_log.py |
| GET `/tools/intent/report` | `services/tool-api/app/api/admin.py:766` | audit.view | test_intent_log.py, test_smoke_api_fixes.py |
| POST `/tools/intent/resolve` | `services/tool-api/app/api/admin.py:728` | любой активный | test_intent_log.py |
| DELETE `/tools/notes/{note_id}` | `services/tool-api/app/api/notes.py:288` | notes.write | test_notes_api.py |
| POST `/tools/notes/{note_id}` | `services/tool-api/app/api/notes.py:278` | notes.write | test_notes_api.py |
| GET `/tools/participants/find` | `services/tool-api/app/api/people.py:574` | participants.view | test_find_person.py, test_registration.py |
| GET `/tools/venues` | `services/tool-api/app/api/events.py:895` | любой активный | test_venues_api.py |
| GET `/webapp/admin/invites` | `services/tool-api/app/api/admin.py:1050` | сессия Mini App + registration.code.issue | test_admin_archive_chat.py, test_admin_invites.py |
| POST `/webapp/admin/sheet-retry` | `services/tool-api/app/api/admin.py:1024` | сессия Mini App + budget.write | test_webapp_sheet_retry.py |
| POST `/webapp/availability/sheet` | `services/tool-api/app/api/schedule.py:1287` | сессия Mini App | нет |
| GET `/webapp/availability/template` | `services/tool-api/app/api/schedule.py:1110` | сессия Mini App | test_gateway_quiet_parity.py |
| GET `/webapp/my` | `services/tool-api/app/api/people.py:318` | сессия Mini App | server.py, test_admin_people_actions.py, test_smoke_api_fixes.py (+1) |
| GET `/webapp/polls/export` | `services/tool-api/app/api/polls.py:663` | — | server.py, test_miniapp_polls.py, test_polls_export_link.py (+3) |

## 9. Самопроверка

* маршрутов в контракте: 264, в коде: 264
* нет в коде, но есть в контракте: 0
* есть в коде, но нет в контракте: 0
<!-- audit-actions:end -->
