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
| Маршрутов (методов) в `contracts/openapi.json` | 234 |
| Маршрутов в коде, которые есть в контракте | 234 |
| Всего маршрутов в коде (с редиректами и ресурсами) | 235 |
| Из них с тестом (по вызову пути в `tests/`) | 221 |
| Действий без права (ни возможности, ни сессии) | 47 |
| Действий с правом, проверенным в теле через `core._webapp_require(…)` | 49 |
| Действий с правом, проверенным в теле через «*» в caps | 2 |
| Маршрутов с сервисным Bearer-токеном на входе | 4 |
| Эндпоинтов, которые не зовёт ни один интерфейс | 29 |
| Экранов Mini App | 30 |
| Обработчиков чата (команды, кнопки, тексты) | 129 |
| Инструментов агента | 13 |

## 2. tool-api: маршруты и права

Права проверяются тремя путями. Первый — зависимость маршрута `Depends(core.require("…"))` (конкретная возможность); вход в сессию объявляют `core.current` (любой активный участник) и `core.webapp_actor` (сессия Mini App, подпись `initData`). Второй — `core._webapp_require(who, "…")` в теле обработчика: та же проверка, что `require()`, но вызванная напрямую (28 маршрутов Mini App, §7a). Третий — `"*" in caps` в теле обработчика (2 маршрута, §7c). Отдельно от прав, на входе, стоит проверка служебного Bearer-токена: `hmac.compare_digest(…, f"Bearer {TOOL_API_TOKEN}")` у маршрутов для другого сервиса (2, §7b). «—» — ни одного из этих путей нет.

| Действие | Точка входа | Интерфейсы | Право | Тест | Примечание |
|---|---|---|---|---|---|
| POST `/admin/approve` | `services/tool-api/app/api/admin.py:292` | чат: admin_decision | registration.approve | test_admin_panel.py, test_uc_admin_flows.py | — |
| POST `/admin/archive` | `services/tool-api/app/api/admin.py:1147` | чат: panel_person_archive, panel_rows | participants.manage | test_admin_archive_chat.py, test_admin_people_actions.py, test_cm01_login.py (+1) | — |
| POST `/admin/block` | `services/tool-api/app/api/admin.py:1276` | чат: _block_person | participants.manage | test_admin_people_actions.py, test_cm01_login.py, test_gateway_chat_parity.py | — |
| POST `/admin/bootstrap` | `services/tool-api/app/api/admin.py:95` | чат: start | сервисный токен | нет | проверка на входе — сервисный Bearer-токен, а не сессия человека |
| GET `/admin/capabilities` | `services/tool-api/app/api/admin.py:326` | чат: panel_person_caps, panel_rights | participants.view | test_admin_panel.py | — |
| POST `/admin/files/cold-rotate` | `services/tool-api/app/api/admin.py:1442` | — | сервисный токен | test_cold.py, test_service_routes_auth.py | проверка на входе — сервисный Bearer-токен, а не сессия человека; никто не зовёт |
| GET `/admin/files/storage` | `services/tool-api/app/api/admin.py:1416` | — | сервисный токен | test_cold.py, test_service_routes_auth.py | проверка на входе — сервисный Bearer-токен, а не сессия человека; никто не зовёт |
| POST `/admin/files/{file_id}/access` | `services/tool-api/app/api/admin.py:1400` | чат: cmd_access | любой активный | test_file_access.py, test_files_actions_screen.py, test_files_studio_roles.py | — |
| POST `/admin/grant` | `services/tool-api/app/api/admin.py:368` | чат: panel_person_cap_toggle, panel_person_master_grant, panel_person_role_toggle, panel_rights, panel_rows | participants.manage | test_admin_panel.py, test_admin_people_actions.py, test_gateway_chat_parity.py (+1) | — |
| POST `/admin/invite` | `services/tool-api/app/api/admin.py:37` | чат: invite_issue, send_invite | registration.code.issue | test_admin_api.py, test_admin_archive_chat.py, test_admin_invites.py (+5) | — |
| POST `/admin/invite/revoke` | `services/tool-api/app/api/admin.py:78` | чат: invite_revoke | registration.code.issue | test_admin_archive_chat.py, test_admin_invites.py, test_gateway_chat_parity.py | — |
| GET `/admin/invites` | `services/tool-api/app/api/admin.py:59` | чат: _send_codes | registration.code.issue | test_admin_archive_chat.py, test_admin_invites.py, test_gateway_chat_parity.py | — |
| POST `/admin/participant/discipline` | `services/tool-api/app/api/admin.py:456` | чат: panel_person_master_link | participants.manage | test_gateway_chat_parity.py, test_master_role.py | — |
| GET `/admin/pending` | `services/tool-api/app/api/admin.py:279` | чат: panel_pending, send_pending | registration.approve | test_admin_api.py, test_admin_panel.py, test_uc_admin_flows.py | — |
| POST `/admin/reconcile` | `services/tool-api/app/api/admin.py:687` | чат: panel_reconcile, reconcile_apply, reconcile_apply_panel, start_reconcile | budget.import | test_admin_api.py | — |
| POST `/admin/unarchive` | `services/tool-api/app/api/admin.py:1199` | чат: panel_person_unarchive | participants.manage | test_admin_archive_chat.py, test_admin_people_actions.py, test_cm01_login.py (+1) | — |
| POST `/agent/actions/{action_id}/apply` | `services/tool-api/app/api/admin.py:1514` | чат: agent_action_cb (по шаблону) | любой активный | нет | — |
| POST `/agent/actions/{action_id}/reject` | `services/tool-api/app/api/admin.py:1526` | чат: agent_action_cb (по шаблону) | любой активный | нет | — |
| GET `/files` | `services/tool-api/app/api/files.py:65` | чат: cmd_files, cmd_search; скрипты: neighbor_counts, restore_dump, tool_list_files (по шаблону) | любой активный | test_action_registry.py, test_files_actions_screen.py, test_files_delete.py (+5) | — |
| POST `/files/upload` | `services/tool-api/app/api/files.py:17` | чат: _delete_card, file_delete_confirm, file_send, upload_to_storage (по шаблону) | любой активный | test_cm07_files.py, test_cold.py, test_file_access.py (+7) | — |
| DELETE `/files/{file_id}` | `services/tool-api/app/api/files.py:93` | чат: _delete_card, file_delete_confirm, file_send, upload_to_storage | любой активный | test_cm07_files.py, test_cold.py, test_file_access.py (+8) | — |
| GET `/files/{file_id}` | `services/tool-api/app/api/files.py:79` | чат: _delete_card, file_delete_confirm, file_send, upload_to_storage | любой активный | test_cm07_files.py, test_cold.py, test_file_access.py (+8) | — |
| GET `/files/{file_id}/access` | `services/tool-api/app/api/files.py:257` | — | любой активный | test_file_access.py, test_files_studio_roles.py | никто не зовёт |
| POST `/files/{file_id}/access/revoke` | `services/tool-api/app/api/files.py:243` | чат: cmd_revoke | любой активный | test_file_access.py, test_files_actions_screen.py, test_gateway_files_rights.py | — |
| GET `/files/{file_id}/content` | `services/tool-api/app/api/files.py:109` | чат: file_send | любой активный | test_cm07_files.py, test_cold.py, test_file_access.py (+2) | — |
| POST `/files/{file_id}/folder` | `services/tool-api/app/api/files.py:441` | — | любой активный | test_files_actions_screen.py, test_folder_order.py | никто не зовёт |
| POST `/files/{file_id}/link` | `services/tool-api/app/api/files.py:149` | чат: file_send | любой активный | test_files_download.py | — |
| GET `/files/{file_id}/tags` | `services/tool-api/app/api/files.py:609` | чат: cmd_tag | любой активный | test_search_tags.py | — |
| POST `/files/{file_id}/tags` | `services/tool-api/app/api/files.py:620` | чат: cmd_tag | любой активный | test_search_tags.py | — |
| GET `/folders` | `services/tool-api/app/api/files.py:187` | чат: cmd_folders, cmd_mkdir; скрипты: neighbor_counts, restore_dump, tool_list_files (по шаблону) | любой активный | test_action_registry.py, test_files_actions_screen.py, test_folders.py (+1) | — |
| POST `/folders` | `services/tool-api/app/api/files.py:200` | чат: cmd_folders, cmd_mkdir; скрипты: neighbor_counts, restore_dump, tool_list_files (по шаблону) | любой активный | test_action_registry.py, test_files_actions_screen.py, test_folders.py (+1) | — |
| POST `/folders/{folder_id}/delete` | `services/tool-api/app/api/files.py:229` | чат: cmd_rmfolder | любой активный | test_folders.py, test_gateway_files_rights.py | — |
| GET `/folders/{folder_id}/files` | `services/tool-api/app/api/files.py:413` | — | любой активный | test_files_actions_screen.py, test_files_delete.py, test_folder_order.py | никто не зовёт |
| POST `/folders/{folder_id}/order` | `services/tool-api/app/api/files.py:429` | чат: cmd_order | любой активный | test_files_actions_screen.py, test_folder_order.py, test_gateway_files_rights.py | — |
| POST `/folders/{folder_id}/rename` | `services/tool-api/app/api/files.py:214` | чат: cmd_rename | любой активный | test_folders.py, test_gateway_files_rights.py | — |
| GET `/folders/{folder_id}/zip` | `services/tool-api/app/api/files.py:463` | чат: cmd_zip | любой активный | test_files_quota.py, test_folder_order.py, test_gateway_files_zip.py | — |
| GET `/health` | `services/tool-api/app/main.py:244` | чат: panel_health, today_local; скрипты: _declared_routes, check_availability, check_health, check_services, fetch_health, health, health_agent, install_launchd, neighbor_counts, restore_dump, start_services (по шаблону) | — | test_arch_drift.py, test_ci_images.py, test_cm01_login.py (+2) | — |
| GET `/tags` | `services/tool-api/app/api/files.py:571` | чат: cmd_tag, cmd_tags; скрипты: neighbor_counts, restore_dump (по шаблону) | любой активный | test_action_registry.py, test_files_actions_screen.py, test_gateway_files_rights.py (+1) | — |
| POST `/tags` | `services/tool-api/app/api/files.py:580` | чат: cmd_tag, cmd_tags; скрипты: neighbor_counts, restore_dump (по шаблону) | любой активный | test_action_registry.py, test_files_actions_screen.py, test_gateway_files_rights.py (+1) | — |
| POST `/tags/{tag_id}/approve` | `services/tool-api/app/api/files.py:595` | чат: cmd_tag_ok | любой активный | test_search_tags.py | — |
| POST `/tools/agent/ask` | `services/tool-api/app/api/agent_ask.py:185` | чат: agent_ask | любой активный | test_agent_ask.py, test_agent_observability.py, test_gateway_agent_ask.py | — |
| POST `/tools/agent/cancel` | `services/tool-api/app/api/agent_confirm.py:222` | чат: agent_cancel, confirm | любой активный | test_agent_confirm.py, test_gateway_agent_confirm.py | — |
| POST `/tools/agent/cancel-task` | `services/tool-api/app/api/agent_confirm.py:118` | чат: agent_cancel_task | любой активный | нет | — |
| POST `/tools/agent/confirm` | `services/tool-api/app/api/agent_confirm.py:179` | чат: agent_confirm, confirm | любой активный | test_agent_confirm.py, test_gateway_agent_confirm.py | — |
| POST `/tools/agent/files` | `services/tool-api/app/api/admin.py:1474` | — | любой активный | нет | никто не зовёт |
| POST `/tools/agent/plan` | `services/tool-api/app/api/agent_confirm.py:86` | чат: agent_plan | любой активный | test_agent_task.py | — |
| POST `/tools/agent/plan/confirm` | `services/tool-api/app/api/agent_confirm.py:96` | чат: agent_plan_confirm | любой активный | test_agent_task.py | — |
| POST `/tools/agent/propose` | `services/tool-api/app/api/agent_confirm.py:73` | чат: agent_propose | любой активный | test_agent_confirm.py | — |
| POST `/tools/assistant/ask` | `services/tool-api/app/api/admin.py:541` | чат: on_text | любой активный | test_cm09_assistant.py, test_gateway_avail_text.py | — |
| GET `/tools/audit/recent` | `services/tool-api/app/api/admin.py:126` | чат: panel_audit | audit.view | test_admin_panel.py, test_budget_idempotent.py, test_cm01_login.py | — |
| POST `/tools/availability/commit` | `services/tool-api/app/api/schedule.py:540` | чат: av_save | schedule.availability.self | test_availability_summary.py, test_availability_week.py, test_cm02_availability.py (+4) | — |
| POST `/tools/availability/parse` | `services/tool-api/app/api/schedule.py:507` | чат: busy_card | любой активный | test_cm02_availability.py, test_gateway_avail_text.py | — |
| POST `/tools/availability/parse-interval` | `services/tool-api/app/api/schedule.py:518` | чат: av_time_text | любой активный | test_gateway_avail_escape.py | — |
| POST `/tools/availability/reconcile` | `services/tool-api/app/api/schedule.py:1487` | — | schedule.availability.self (в теле — schedule.read.all) | test_availability_sheet.py | проверка права в теле обработчика (через «*»); никто не зовёт |
| POST `/tools/availability/sheet` | `services/tool-api/app/api/schedule.py:1256` | — | schedule.availability.self | test_availability_sheet.py | никто не зовёт |
| GET `/tools/availability/sheet/mismatches` | `services/tool-api/app/api/schedule.py:1444` | — | schedule.availability.self | test_availability_sheet.py | никто не зовёт |
| GET `/tools/availability/summary` | `services/tool-api/app/api/schedule.py:562` | чат: answer_schedule | schedule.read.all | test_availability_summary.py, test_cm02_availability.py, test_smoke_fixes.py (+1) | — |
| GET `/tools/availability/template` | `services/tool-api/app/api/schedule.py:932` | чат: _week_view, week_text | schedule.availability.self | test_availability_week.py, test_gateway_avail_parity.py | — |
| POST `/tools/availability/template` | `services/tool-api/app/api/schedule.py:947` | чат: _week_view, week_text | schedule.availability.self | test_availability_week.py, test_gateway_avail_parity.py | — |
| POST `/tools/availability/template/apply` | `services/tool-api/app/api/schedule.py:1002` | чат: week_action | schedule.availability.self | test_availability_week.py, test_gateway_avail_parity.py | — |
| POST `/tools/availability/template/from-week` | `services/tool-api/app/api/schedule.py:959` | чат: week_action | schedule.availability.self | test_availability_week.py, test_cm02_availability.py, test_gateway_avail_parity.py (+1) | — |
| GET `/tools/availability/week` | `services/tool-api/app/api/schedule.py:733` | чат: av_save; скрипты: tool_read_availability | schedule.availability.self (в теле — schedule.read.all) | test_availability_sheet.py, test_availability_week.py, test_cm02_availability.py (+2) | проверка права в теле обработчика (через «*») |
| POST `/tools/availability/week` | `services/tool-api/app/api/schedule.py:812` | чат: av_save; скрипты: tool_read_availability | schedule.availability.self | test_availability_sheet.py, test_availability_week.py, test_cm02_availability.py (+2) | — |
| POST `/tools/budget/commit` | `services/tool-api/app/api/budget.py:237` | чат: budget_confirm, confirm, form_save | budget.write | test_budget_idempotent.py, test_budget_recent_period.py, test_category_direction.py (+6) | — |
| POST `/tools/budget/parse` | `services/tool-api/app/api/budget.py:464` | чат: parse_budget | любой активный | test_cm04_money.py, test_cm09_assistant.py, test_gateway_assign_dialog.py (+2) | — |
| GET `/tools/budget/recent` | `services/tool-api/app/api/budget.py:375` | чат: panel_money, periods | budget.read | test_budget_idempotent.py, test_budget_recent_period.py, test_smoke_api_fixes.py | — |
| POST `/tools/budget/sheet-retry` | `services/tool-api/app/api/budget.py:291` | чат: panel_sheet_retry | budget.write | test_sheet_retry.py, test_webapp_sheet_retry.py | — |
| GET `/tools/budget/summary` | `services/tool-api/app/api/budget.py:475` | чат: answer_money; скрипты: tool_read_budget | budget.read | test_budget_summary.py, test_cm04_money.py, test_cm09_assistant.py (+4) | — |
| GET `/tools/calendar/events` | `services/tool-api/app/api/events.py:852` | чат: answer_schedule; скрипты: tool_read_schedule | schedule.read.all | test_calendar_api.py, test_cm11_degradation.py, test_smoke_fixes.py (+2) | — |
| GET `/tools/calendar/health` | `services/tool-api/app/api/events.py:825` | — | любой активный | test_calendar_api.py | никто не зовёт |
| GET `/tools/categories` | `services/tool-api/app/api/budget.py:415` | чат: form_amount | любой активный | test_ci_images.py | — |
| GET `/tools/disciplines` | `services/tool-api/app/api/events.py:809` | чат: panel_person_master | любой активный | test_disciplines.py, test_gateway_chat_parity.py | — |
| POST `/tools/dues/accrue` | `services/tool-api/app/api/finance.py:65` | — | budget.write | test_cm04_money.py, test_dues_accrue.py | никто не зовёт |
| GET `/tools/dues/me` | `services/tool-api/app/api/finance.py:24` | чат: answer_profile, cmd_dues, my_dues, run_menu_action, show_dues; скрипты: tool_read_dues | budget.read.self | test_cm04_money.py, test_dues_admin_ui.py, test_dues_tariff.py (+2) | — |
| POST `/tools/events/assign` | `services/tool-api/app/api/events.py:396` | чат: _load, assign_confirm (по шаблону) | schedule.write | test_cm03_slots.py, test_event_chat_card.py, test_events_assign.py (+1) | — |
| POST `/tools/events/cancel` | `services/tool-api/app/api/events.py:531` | чат: _cancel, _load (по шаблону) | schedule.write | test_cm03_slots.py, test_event_cancel.py, test_event_chat_card.py | — |
| POST `/tools/events/choose` | `services/tool-api/app/api/events.py:350` | чат: _load, assign_pick (по шаблону) | schedule.write | test_events_choose.py | — |
| POST `/tools/events/move` | `services/tool-api/app/api/events.py:639` | чат: _load, _move (по шаблону) | schedule.write | test_cm03_slots.py, test_event_move.py | — |
| POST `/tools/events/preview` | `services/tool-api/app/api/events.py:315` | чат: _load, preview (по шаблону) | schedule.write | test_disciplines.py, test_events_assign.py, test_events_choose.py (+6) | — |
| GET `/tools/events/{event_id}` | `services/tool-api/app/api/events.py:763` | чат: _cancel, _load, _move, assign_confirm, assign_pick, preview | schedule.write | test_cm03_slots.py, test_disciplines.py, test_event_cancel.py (+10) | — |
| GET `/tools/feedback` | `services/tool-api/app/api/admin.py:180` | чат: feedback_triage, panel_feedback, send_feedback | feedback.triage | test_cm06_feedback.py, test_feedback.py, test_feedback_digest.py (+1) | — |
| POST `/tools/feedback` | `services/tool-api/app/api/admin.py:147` | чат: feedback_triage, panel_feedback, send_feedback | любой активный | test_cm06_feedback.py, test_feedback.py, test_feedback_digest.py (+1) | — |
| POST `/tools/feedback/decide` | `services/tool-api/app/api/admin.py:217` | чат: feedback_decide; скрипты: decide | feedback.triage | test_cm06_feedback.py, test_feedback.py, test_feedback_digest.py | — |
| GET `/tools/feedback/mine` | `services/tool-api/app/api/admin.py:167` | — | любой активный | test_cm06_feedback.py | никто не зовёт |
| GET `/tools/feedback/open` | `services/tool-api/app/api/feedback.py:68` | — | feedback.triage | test_feedback_triage.py | никто не зовёт |
| POST `/tools/feedback/triage` | `services/tool-api/app/api/feedback.py:111` | скрипты: main | feedback.triage | test_feedback_triage.py | — |
| GET `/tools/finance/granularity` | `services/tool-api/app/api/finance.py:190` | — | budget.read | нет | никто не зовёт |
| POST `/tools/finance/granularity` | `services/tool-api/app/api/finance.py:200` | — | budget.import | нет | никто не зовёт |
| GET `/tools/finance/sources` | `services/tool-api/app/api/finance.py:224` | — | budget.read | нет | никто не зовёт |
| POST `/tools/finance/sources` | `services/tool-api/app/api/finance.py:241` | — | budget.import | нет | никто не зовёт |
| DELETE `/tools/finance/sources/{source_id}` | `services/tool-api/app/api/finance.py:260` | — | budget.import | нет | никто не зовёт |
| GET `/tools/howto` | `services/tool-api/app/api/webapp_api.py:173` | чат: run_menu_action | howto.read | test_howto.py | — |
| GET `/tools/intent/categories` | `services/tool-api/app/api/admin.py:622` | — | любой активный | test_intent_log.py | никто не зовёт |
| GET `/tools/intent/report` | `services/tool-api/app/api/admin.py:670` | — | audit.view | test_intent_log.py, test_smoke_api_fixes.py | никто не зовёт |
| POST `/tools/intent/resolve` | `services/tool-api/app/api/admin.py:632` | — | любой активный | test_intent_log.py | никто не зовёт |
| GET `/tools/me/capabilities` | `services/tool-api/app/api/people.py:678` | чат: caps_of | любой активный | test_admin_people_actions.py, test_cm01_login.py | — |
| GET `/tools/notes` | `services/tool-api/app/api/notes.py:235` | чат: note_confirm | notes.write | test_cm07_files.py, test_cm12_audit_cleanup.py, test_gateway_notes.py (+1) | — |
| POST `/tools/notes` | `services/tool-api/app/api/notes.py:244` | чат: note_confirm | notes.write | test_cm07_files.py, test_cm12_audit_cleanup.py, test_gateway_notes.py (+1) | — |
| POST `/tools/notes/{note_id}` | `services/tool-api/app/api/notes.py:253` | — | notes.write | test_notes_api.py | никто не зовёт |
| GET `/tools/notifications/settings` | `services/tool-api/app/api/webapp_api.py:410` | чат: _quiet_settings, _save_quiet | любой активный | test_cm05_notifications.py, test_gateway_quiet.py, test_gateway_quiet_parity.py | — |
| POST `/tools/notifications/settings` | `services/tool-api/app/api/webapp_api.py:422` | чат: _quiet_settings, _save_quiet | любой активный | test_cm05_notifications.py, test_gateway_quiet.py, test_gateway_quiet_parity.py | — |
| GET `/tools/participants` | `services/tool-api/app/api/people.py:126` | чат: _send_archive, list_people, panel_people, send_people | participants.view | test_admin_api.py, test_admin_archive_chat.py, test_cm01_login.py (+6) | — |
| POST `/tools/participants/attach` | `services/tool-api/app/api/people.py:639` | чат: reg_pick | — | test_intro.py, test_registration.py | — |
| POST `/tools/participants/check_code` | `services/tool-api/app/api/people.py:583` | чат: on_text | — | test_admin_invites.py, test_gateway_registration.py, test_registration.py (+1) | — |
| GET `/tools/participants/find` | `services/tool-api/app/api/people.py:624` | — | participants.view | test_find_person.py | никто не зовёт |
| GET `/tools/participants/me` | `services/tool-api/app/api/people.py:117` | чат: about_me, answer_profile, disciplines_line, start, user_ctx | любой активный | test_gateway_chat_parity.py, test_gateway_menu_rights.py, test_master_role.py (+2) | — |
| POST `/tools/participants/register` | `services/tool-api/app/api/people.py:43` | чат: reg_name | — | test_cm01_login.py, test_registration.py | — |
| GET `/tools/participants/search` | `services/tool-api/app/api/people.py:606` | чат: reg_filter | сервисный токен | test_gateway_registration.py, test_registration.py | проверка на входе — сервисный Bearer-токен, а не сессия человека |
| GET `/tools/participants/{participant_id}/card` | `services/tool-api/app/api/people.py:30` | чат: _card_of, panel_person, panel_person_caps, panel_person_roles, panel_person_tariff | participants.view | test_gateway_chat_parity.py, test_master_role.py, test_participants_card.py (+1) | — |
| GET `/tools/participants/{participant_id}/dues-tariff` | `services/tool-api/app/api/people.py:718` | чат: panel_tariff_save | participants.view | test_dues_tariff.py | — |
| POST `/tools/participants/{participant_id}/dues-tariff` | `services/tool-api/app/api/people.py:735` | чат: panel_tariff_save | budget.write | test_dues_tariff.py | — |
| POST `/tools/profile/intro-parse` | `services/tool-api/app/api/people.py:344` | чат: intro_answer | любой активный | test_intro.py, test_intro_llm_api.py | — |
| GET `/tools/profile/me` | `services/tool-api/app/api/people.py:181` | чат: intro_save, intro_start, profile_cmd | любой активный | test_availability_week.py, test_cm01_login.py, test_cm11_degradation.py (+4) | — |
| POST `/tools/profile/me` | `services/tool-api/app/api/people.py:254` | чат: intro_save, intro_start, profile_cmd | любой активный | test_availability_week.py, test_cm01_login.py, test_cm11_degradation.py (+4) | — |
| GET `/tools/quota/me` | `services/tool-api/app/api/quota.py:86` | чат: answer_quota | любой активный | test_quota_me.py | — |
| POST `/tools/route` | `services/tool-api/app/api/route.py:74` | чат: router_ask; скрипты: agent_eval, ask | любой активный | test_cm09_assistant.py, test_cm11_degradation.py, test_routes_contract.py (+1) | — |
| POST `/tools/schedule/slots` | `services/tool-api/app/api/schedule.py:244` | чат: answer_slots | schedule.slots | test_agent_slots_tool.py, test_cm03_slots.py, test_gateway_slots_entry.py (+7) | — |
| GET `/tools/ui-texts` | `services/tool-api/app/api/webapp_api.py:189` | чат: ui_texts_cached | любой активный | нет | — |
| GET `/tools/venues` | `services/tool-api/app/api/events.py:818` | — | любой активный | test_venues_api.py | никто не зовёт |
| GET `/webapp` | `services/tool-api/app/pages/routes.py:97` | Mini App: home, routes; чат: profile_cmd, run_menu_action; скрипты: neighbor_counts, restore_dump, ui_review, ux_audit (по шаблону) | — | server.py, test_agent_features.py, test_miniapp_visual.py (+4) | экран Mini App: HTML без данных |
| GET `/webapp/admin` | `services/tool-api/app/pages/routes.py:169` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_static_contract.py, test_webapp_admin.py | — |
| GET `/webapp/admin/agent` | `services/tool-api/app/api/admin.py:1102` | Mini App: admin_agent | сессия Mini App + admin.panel | server.py, test_agent_observability.py | право проверяется в теле обработчика, а не в зависимости: admin.panel |
| POST `/webapp/admin/approve` | `services/tool-api/app/api/admin.py:838` | Mini App: admin_pending | сессия Mini App + registration.approve | test_webapp_admin_actions.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: registration.approve |
| POST `/webapp/admin/archive` | `services/tool-api/app/api/admin.py:1142` | Mini App: admin_archive, admin_people | сессия Mini App + participants.manage | test_admin_archive_chat.py, test_admin_people_actions.py, test_miniapp_archive_rights.py (+2) | право проверяется в теле обработчика, а не в зависимости: participants.manage |
| POST `/webapp/admin/block` | `services/tool-api/app/api/admin.py:1270` | Mini App: admin_people | сессия Mini App + participants.manage | test_admin_people_actions.py, test_webapp_pages_js.py | право проверяется в теле обработчика, а не в зависимости: participants.manage |
| POST `/webapp/admin/broadcast` | `services/tool-api/app/api/admin.py:1364` | Mini App: misc_broadcast | сессия Mini App + notify.send | server.py, test_broadcast_rights.py, test_cm05_notifications.py | право проверяется в теле обработчика, а не в зависимости: notify.send |
| GET `/webapp/admin/broadcast/preview` | `services/tool-api/app/api/admin.py:1346` | Mini App: misc_broadcast | сессия Mini App + notify.send | server.py, test_broadcast_rights.py, test_cm05_notifications.py (+1) | право проверяется в теле обработчика, а не в зависимости: notify.send |
| POST `/webapp/admin/discipline` | `services/tool-api/app/api/admin.py:852` | Mini App: admin_disciplines | сессия Mini App + participants.manage | нет | право проверяется в теле обработчика, а не в зависимости: participants.manage |
| GET `/webapp/admin/dues` | `services/tool-api/app/api/finance.py:110` | Mini App: admin_dues | сессия Mini App + budget.write | server.py, test_dues_admin_ui.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: budget.write |
| POST `/webapp/admin/dues/accrue` | `services/tool-api/app/api/finance.py:174` | Mini App: admin_dues | сессия Mini App + budget.write | server.py, test_dues_admin_ui.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: budget.write |
| GET `/webapp/admin/feedback` | `services/tool-api/app/api/admin.py:925` | Mini App: admin_feedback | сессия Mini App + feedback.triage | server.py | право проверяется в теле обработчика, а не в зависимости: feedback.triage |
| POST `/webapp/admin/feedback/decide` | `services/tool-api/app/api/admin.py:937` | Mini App: admin_feedback | сессия Mini App + feedback.triage | test_feedback_open_only.py, test_webapp_merge_deps.py | право проверяется в теле обработчика, а не в зависимости: feedback.triage |
| POST `/webapp/admin/grant` | `services/tool-api/app/api/admin.py:845` | Mini App: admin_people, admin_rights | сессия Mini App + participants.manage | test_webapp_admin_actions.py, test_webapp_admin_pages.py, test_webapp_pages_js.py | право проверяется в теле обработчика, а не в зависимости: participants.manage |
| POST `/webapp/admin/invite` | `services/tool-api/app/api/admin.py:908` | Mini App: admin_invites | сессия Mini App + registration.code.issue | test_admin_invites.py, test_webapp_admin_pagination.py | право проверяется в теле обработчика, а не в зависимости: registration.code.issue |
| POST `/webapp/admin/invite-revoke` | `services/tool-api/app/api/admin.py:918` | Mini App: admin_invites | сессия Mini App + registration.code.issue | test_admin_invites.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: registration.code.issue |
| GET `/webapp/admin/invites` | `services/tool-api/app/api/admin.py:894` | — | сессия Mini App + registration.code.issue | test_admin_invites.py | право проверяется в теле обработчика, а не в зависимости: registration.code.issue; никто не зовёт |
| GET `/webapp/admin/notifications/settings` | `services/tool-api/app/api/admin.py:1305` | Mini App: admin_notifications | сессия Mini App + admin.panel | server.py, test_notification_defaults.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: admin.panel |
| POST `/webapp/admin/notifications/settings` | `services/tool-api/app/api/admin.py:1315` | Mini App: admin_notifications | сессия Mini App + admin.panel | server.py, test_notification_defaults.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: admin.panel |
| GET `/webapp/admin/overview` | `services/tool-api/app/api/admin.py:760` | Mini App: admin_archive, admin_core, admin_feedback, admin_invites, admin_rights, money_script | сессия Mini App | server.py, test_miniapp_archive_rights.py, test_miniapp_audit_rights.py (+8) | — |
| POST `/webapp/admin/person` | `services/tool-api/app/api/admin.py:885` | Mini App: admin_people, admin_rights | сессия Mini App + participants.view | test_participants_card.py, test_webapp_admin_pages.py | право проверяется в теле обработчика, а не в зависимости: participants.view |
| POST `/webapp/admin/reconcile` | `services/tool-api/app/api/admin.py:264` | Mini App: misc_reconcile | сессия Mini App + budget.import | server.py | право проверяется в теле обработчика, а не в зависимости: budget.import |
| GET `/webapp/admin/services` | `services/tool-api/app/api/admin.py:1053` | Mini App: services | сессия Mini App + admin.panel | server.py, test_cm08_miniapp.py, test_cm11_degradation.py | право проверяется в теле обработчика, а не в зависимости: admin.panel |
| POST `/webapp/admin/sheet-retry` | `services/tool-api/app/api/admin.py:868` | — | сессия Mini App + budget.write | test_webapp_sheet_retry.py | право проверяется в теле обработчика, а не в зависимости: budget.write; никто не зовёт |
| GET `/webapp/admin/t/agent` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: intent_report, ui_review, ux_audit | — | server.py, test_agent_observability.py, test_miniapp_hub.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/archive` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_archive_rights.py, test_miniapp_hub.py (+3) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/audit` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_miniapp_audit_rights.py, test_miniapp_hub.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/budget` | `services/tool-api/app/pages/routes.py:181` | Mini App: routes (по шаблону) | — | server.py, test_miniapp_hub.py | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/dues` | `services/tool-api/app/pages/routes.py:176` | Mini App: admin_people, routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_ui_review.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/feedback` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_feedback_open_only.py, test_miniapp_feedback_rights.py (+2) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/invites` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_miniapp_invites_rights.py (+2) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/notifications` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_ui_review.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/pending` | `services/tool-api/app/pages/routes.py:176` | Mini App: home, routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_pending_rights.py (+2) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/people` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_js_behavior.py (+4) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/rights` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_miniapp_rights_screen.py (+2) | раздел из реестра app/webapp.py:SECTIONS |
| POST `/webapp/admin/tariff` | `services/tool-api/app/api/admin.py:860` | Mini App: admin_dues, admin_people | сессия Mini App + budget.write | test_dues_admin_ui.py, test_webapp_admin_actions.py, test_webapp_admin_pages.py (+1) | право проверяется в теле обработчика, а не в зависимости: budget.write |
| POST `/webapp/admin/unarchive` | `services/tool-api/app/api/admin.py:1194` | Mini App: admin_archive | сессия Mini App + participants.manage | test_admin_archive_chat.py, test_admin_people_actions.py, test_miniapp_archive_rights.py (+1) | право проверяется в теле обработчика, а не в зависимости: participants.manage |
| GET `/webapp/assign` | `services/tool-api/app/pages/routes.py:217` | Mini App: misc_slots, routes; скрипты: ui_review | — | server.py, test_agent_task.py, test_gateway_agent_plan.py (+1) | — |
| POST `/webapp/assign` | `services/tool-api/app/api/events.py:894` | Mini App: misc_slots, routes; скрипты: ui_review | сессия Mini App | server.py, test_agent_task.py, test_gateway_agent_plan.py (+1) | — |
| POST `/webapp/auth` | `services/tool-api/app/api/webapp_api.py:35` | Mini App: webapp-shell.js; скрипты: issue_tokens | — | server.py, test_admin_invites.py, test_admin_people_actions.py (+30) | — |
| POST `/webapp/availability/clear` | `services/tool-api/app/api/schedule.py:1618` | Mini App: week_marks, week_sheet_load, week_template | сессия Mini App | server.py, test_webapp_availability_clear.py, test_webapp_grid.py (+1) | — |
| POST `/webapp/availability/sheet` | `services/tool-api/app/api/schedule.py:1264` | — | сессия Mini App | нет | никто не зовёт |
| POST `/webapp/availability/sheet/load` | `services/tool-api/app/api/schedule.py:1431` | Mini App: week_sheet_load | сессия Mini App | server.py, test_webapp_sheet_load.py | — |
| GET `/webapp/availability/sheet/preview` | `services/tool-api/app/api/schedule.py:1344` | Mini App: week_sheet_load | сессия Mini App | server.py, test_miniapp_sheet_load.py, test_webapp_sheet_load.py | — |
| GET `/webapp/availability/template` | `services/tool-api/app/api/schedule.py:1098` | — | сессия Mini App | test_gateway_quiet_parity.py | никто не зовёт |
| POST `/webapp/availability/template/apply` | `services/tool-api/app/api/schedule.py:1115` | Mini App: week_template | сессия Mini App | server.py, test_availability_week.py, test_webapp_week_page.py | — |
| POST `/webapp/availability/template/from-week` | `services/tool-api/app/api/schedule.py:1106` | Mini App: week_template | сессия Mini App | server.py, test_availability_week.py, test_webapp_week_page.py | — |
| GET `/webapp/availability/week` | `services/tool-api/app/api/schedule.py:1081` | Mini App: week_marks, week_sheet, week_sheet_load; чат: av_save | сессия Mini App | server.py, test_availability_week.py, test_gateway_quiet_parity.py (+4) | — |
| POST `/webapp/availability/week` | `services/tool-api/app/api/schedule.py:1090` | Mini App: week_marks, week_sheet, week_sheet_load; чат: av_save | сессия Mini App | server.py, test_availability_week.py, test_gateway_quiet_parity.py (+4) | — |
| POST `/webapp/boot` | `services/tool-api/app/api/webapp_api.py:87` | Mini App: layout, webapp-shell.js | — | test_miniapp_net_guard.py, test_webapp_boot.py | — |
| GET `/webapp/broadcast` | `services/tool-api/app/pages/routes.py:132` | Mini App: routes; скрипты: ui_review, ux_audit | — | server.py, test_broadcast_rights.py, test_miniapp_broadcast.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/categories` | `services/tool-api/app/api/budget.py:423` | Mini App: money_tx_script | сессия Mini App | server.py | — |
| GET `/webapp/days` | `services/tool-api/app/pages/routes.py:202` | Mini App: routes; чат: _buttons_with_week, send_menu; скрипты: ui_review | — | test_miniapp_system.py, test_static_contract.py, test_webapp_merge_deps.py (+1) | — |
| GET `/webapp/dues` | `services/tool-api/app/pages/routes.py:107` | Mini App: kit, routes; скрипты: ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_visual.py | экран Mini App: HTML без данных |
| GET `/webapp/feedback` | `services/tool-api/app/pages/routes.py:117` | Mini App: home, misc_feedback, routes; скрипты: ui_review, ux_audit | — | server.py, test_feedback.py, test_miniapp_js_behavior.py (+1) | экран Mini App: HTML без данных |
| POST `/webapp/feedback` | `services/tool-api/app/api/admin.py:731` | Mini App: home, misc_feedback, routes; скрипты: ui_review, ux_audit | сессия Mini App + feedback.send | server.py, test_feedback.py, test_miniapp_js_behavior.py (+1) | право проверяется в теле обработчика, а не в зависимости: feedback.send |
| GET `/webapp/feedback/mine` | `services/tool-api/app/api/admin.py:742` | Mini App: misc_feedback | сессия Mini App | server.py, test_feedback.py, test_webapp_misc_pages.py | — |
| GET `/webapp/files` | `services/tool-api/app/pages/routes.py:147` | Mini App: files_actions, files_folder, home, routes; скрипты: ui_review | — | server.py, test_agent_task.py, test_files_studio_roles.py (+7) | экран Mini App: HTML без данных |
| POST `/webapp/files/commit` | `services/tool-api/app/api/files.py:326` | Mini App: files | сессия Mini App + files.access | test_files_delete.py, test_webapp_files.py, test_webapp_files_pages.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/files/list` | `services/tool-api/app/api/files.py:282` | Mini App: files | сессия Mini App + files.access | server.py, test_files_delete.py, test_files_studio_roles.py (+2) | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/files/presign` | `services/tool-api/app/api/files.py:295` | Mini App: files | сессия Mini App + files.access | test_files_delete.py, test_webapp_files.py, test_webapp_files_pages.py (+1) | право проверяется в теле обработчика, а не в зависимости: files.access |
| DELETE `/webapp/files/{file_id}` | `services/tool-api/app/api/files.py:370` | Mini App: files | сессия Mini App + files.access | server.py, test_files_delete.py, test_files_studio_roles.py (+3) | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/files/{file_id}/access` | `services/tool-api/app/api/files.py:722` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py, test_files_studio_roles.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/files/{file_id}/access` | `services/tool-api/app/api/files.py:697` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py, test_files_studio_roles.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/files/{file_id}/access/revoke` | `services/tool-api/app/api/files.py:711` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/folder` | `services/tool-api/app/pages/routes.py:157` | Mini App: files, files_actions, routes; чат: _zip_refusal_text, cmd_zip; скрипты: ui_review | — | server.py, test_miniapp_shell_contract.py, test_miniapp_visual.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/folder/{folder_id}/files` | `services/tool-api/app/api/files.py:502` | Mini App: files_folder | сессия Mini App + files.access | server.py, test_folder_order.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/folder/{folder_id}/order` | `services/tool-api/app/api/files.py:518` | Mini App: files_folder | сессия Mini App + files.access | test_files_actions_screen.py, test_folder_order.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/folder/{folder_id}/zip` | `services/tool-api/app/api/files.py:531` | Mini App: files_folder | сессия Mini App + files.access | нет | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/folders` | `services/tool-api/app/api/files.py:652` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/folders` | `services/tool-api/app/api/files.py:662` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/folders/{folder_id}/delete` | `services/tool-api/app/api/files.py:686` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/folders/{folder_id}/rename` | `services/tool-api/app/api/files.py:674` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/form-data` | `services/tool-api/app/api/webapp_api.py:138` | Mini App: admin_disciplines | сессия Mini App | test_webapp_search.py | — |
| GET `/webapp/grid` | `services/tool-api/app/api/schedule.py:49` | Mini App: week_summary | сессия Mini App + schedule.read.all | server.py, test_cm10_reports.py, test_miniapp_system.py (+3) | право проверяется в теле обработчика, а не в зависимости: schedule.read.all |
| GET `/webapp/home` | `services/tool-api/app/pages/routes.py:65` | Mini App: kit, layout, person, routes, webapp-shell.js; чат: _show_open_card, ensure_menu_button, main, with_home; скрипты: ui_review, ux_audit | — | server.py, test_agent_task.py, test_miniapp_home_loading.py (+7) | экран Mini App: HTML без данных |
| GET `/webapp/home/data` | `services/tool-api/app/api/webapp_api.py:279` | Mini App: home, home_loading, money_script, money_tx, money_tx_rights, routes | сессия Mini App | server.py, test_broadcast_rights.py, test_cm08_miniapp.py (+6) | — |
| GET `/webapp/money` | `services/tool-api/app/pages/routes.py:90` | Mini App: layout, misc_dues, money_script, money_tx, money_tx_script, routes; скрипты: ui_review, ux_audit | — | server.py, test_cm08_miniapp.py, test_cm10_reports.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/month-grid` | `services/tool-api/app/api/schedule.py:111` | Mini App: month | сессия Mini App + schedule.read.all | server.py, test_webapp_month_grid.py, test_webapp_week_page.py | право проверяется в теле обработчика, а не в зависимости: schedule.read.all |
| GET `/webapp/more` | `services/tool-api/app/pages/routes.py:70` | Mini App: admin_core, files, home, home_loading, kit, layout, misc_broadcast, misc_feedback, misc_notifications, misc_profile, misc_reconcile, notes, routes, services, webapp-shell.js; скрипты: ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_webapp_assets.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/my` | `services/tool-api/app/api/people.py:381` | — | сессия Mini App | server.py, test_admin_people_actions.py, test_smoke_api_fixes.py (+1) | никто не зовёт |
| GET `/webapp/my/dues` | `services/tool-api/app/api/finance.py:17` | Mini App: misc_dues, money_script | сессия Mini App | server.py, test_dues_admin_ui.py, test_dues_tariff.py | — |
| GET `/webapp/my/sheet` | `services/tool-api/app/pages/routes.py:207` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_webapp_merge_deps.py | — |
| GET `/webapp/nav` | `services/tool-api/app/pages/routes.py:58` | Mini App: home_loading, layout, misc_reconcile, routes, webapp-shell.js, week_template | сессия Mini App | server.py, test_miniapp_net_guard.py, test_miniapp_reconcile_rights.py (+6) | — |
| GET `/webapp/notes` | `services/tool-api/app/pages/routes.py:152` | Mini App: routes; чат: cmd_notes, note_start, notes; скрипты: ui_review | — | server.py, test_agent_task.py, test_miniapp_notes.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/notes/list` | `services/tool-api/app/api/notes.py:263` | Mini App: notes | сессия Mini App + notes.write | server.py, test_notes_api.py | право проверяется в теле обработчика, а не в зависимости: notes.write |
| POST `/webapp/notes/save` | `services/tool-api/app/api/notes.py:272` | Mini App: notes | сессия Mini App + notes.write | server.py, test_cm08_miniapp.py, test_notes_api.py | право проверяется в теле обработчика, а не в зависимости: notes.write |
| GET `/webapp/notifications` | `services/tool-api/app/pages/routes.py:112` | Mini App: admin_notifications, home, routes; чат: quiet_text; скрипты: ui_review, ux_audit | — | server.py, test_gateway_quiet.py, test_miniapp_hub.py | экран Mini App: HTML без данных |
| GET `/webapp/notifications/settings` | `services/tool-api/app/api/webapp_api.py:397` | Mini App: misc_notifications | сессия Mini App | server.py, test_gateway_quiet_parity.py, test_notification_defaults.py | — |
| POST `/webapp/notifications/settings` | `services/tool-api/app/api/webapp_api.py:403` | Mini App: misc_notifications | сессия Mini App | server.py, test_gateway_quiet_parity.py, test_notification_defaults.py | — |
| GET `/webapp/people` | `services/tool-api/app/pages/routes.py:212` | Mini App: routes; скрипты: ui_review | — | test_agent_task.py | — |
| GET `/webapp/person` | `services/tool-api/app/pages/routes.py:162` | Mini App: person, routes; чат: panel_person; скрипты: ui_review | — | server.py, test_miniapp_js_behavior.py, test_miniapp_system.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/person/info` | `services/tool-api/app/api/people.py:684` | Mini App: person | сессия Mini App + participants.view | server.py, test_org_miniapp_screens.py | право проверяется в теле обработчика, а не в зависимости: participants.view |
| POST `/webapp/person/tariff` | `services/tool-api/app/api/people.py:710` | Mini App: person | сессия Mini App + budget.write | server.py, test_org_miniapp_screens.py | право проверяется в теле обработчика, а не в зависимости: budget.write |
| POST `/webapp/preview` | `services/tool-api/app/api/events.py:887` | Mini App: misc_slots | сессия Mini App | server.py, test_miniapp_slots.py, test_webapp_search.py | — |
| GET `/webapp/profile` | `services/tool-api/app/api/people.py:427` | Mini App: misc_profile | сессия Mini App | server.py, test_webapp_profile.py | — |
| POST `/webapp/profile` | `services/tool-api/app/api/people.py:458` | Mini App: misc_profile | сессия Mini App | server.py, test_webapp_profile.py | — |
| GET `/webapp/reconcile` | `services/tool-api/app/pages/routes.py:127` | Mini App: layout, routes; скрипты: ui_review, ux_audit | — | server.py, test_miniapp_reconcile_rights.py, test_webapp_layout.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/schedule` | `services/tool-api/app/pages/routes.py:222` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_ui_review.py | — |
| GET `/webapp/search` | `services/tool-api/app/pages/routes.py:197` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_webapp_search.py | — |
| GET `/webapp/services` | `services/tool-api/app/pages/routes.py:142` | Mini App: routes; скрипты: ui_review, ux_audit | — | server.py, test_webapp_screen_rights.py, test_webapp_sections.py (+1) | экран Mini App: HTML без данных |
| GET `/webapp/shell.js` | `services/tool-api/app/api/webapp_api.py:112` | Mini App: layout | — | server.py, test_routes_contract.py, test_static_contract.py (+4) | — |
| GET `/webapp/slots` | `services/tool-api/app/pages/routes.py:122` | Mini App: misc_slots_denied, routes, week_summary; чат: answer_slots, availability, run_menu_action; скрипты: ui_review, ux_audit | — | server.py, test_agent_task.py, test_gateway_slots_entry.py (+3) | экран Mini App: HTML без данных |
| POST `/webapp/slots` | `services/tool-api/app/api/schedule.py:1606` | Mini App: misc_slots_denied, routes, week_summary; чат: answer_slots, availability, run_menu_action; скрипты: ui_review, ux_audit | сессия Mini App | server.py, test_agent_task.py, test_gateway_slots_entry.py (+3) | — |
| POST `/webapp/slots/find` | `services/tool-api/app/api/schedule.py:1059` | Mini App: misc_slots | сессия Mini App + schedule.slots | server.py, test_org_miniapp_screens.py | право проверяется в теле обработчика, а не в зависимости: schedule.slots |
| GET `/webapp/slots/people` | `services/tool-api/app/api/schedule.py:1027` | Mini App: misc_slots | сессия Mini App + schedule.slots | server.py, test_master_role.py, test_org_miniapp_screens.py | право проверяется в теле обработчика, а не в зависимости: schedule.slots |
| GET `/webapp/tags/pending` | `services/tool-api/app/api/files.py:738` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| POST `/webapp/tags/{tag_id}/approve` | `services/tool-api/app/api/files.py:747` | Mini App: files_actions | сессия Mini App + files.access | server.py, test_files_actions_screen.py | право проверяется в теле обработчика, а не в зависимости: files.access |
| GET `/webapp/tg.js` | `services/tool-api/app/api/webapp_api.py:60` | Mini App: layout | — | server.py, test_cm08_miniapp.py, test_webapp_assets.py (+1) | — |
| GET `/webapp/tx` | `services/tool-api/app/pages/routes.py:102` | Mini App: home_loading, money, money_script, routes; чат: run_menu_action; скрипты: ui_review, ux_audit | — | server.py, test_agent_task.py, test_miniapp_js_behavior.py (+2) | экран Mini App: HTML без данных |
| POST `/webapp/tx/save` | `services/tool-api/app/api/budget.py:452` | Mini App: money_tx_script, routes | сессия Mini App + budget.write | server.py, test_cm08_miniapp.py, test_org_miniapp_screens.py | право проверяется в теле обработчика, а не в зависимости: budget.write |
| GET `/webapp/ui` | `services/tool-api/app/pages/routes.py:137` | Mini App: kit, routes; скрипты: ui_review, ux_audit | — | server.py, test_miniapp_home_loading.py, test_miniapp_hub.py | экран Mini App: HTML без данных |
| GET `/webapp/ui.css` | `services/tool-api/app/api/webapp_api.py:125` | Mini App: layout, webapp-shell.js | — | server.py, test_cm11_degradation.py, test_routes_contract.py (+3) | — |
| GET `/webapp/week` | `services/tool-api/app/pages/routes.py:75` | Mini App: home_loading, kit, layout, misc_slots, routes, week_sheet, week_summary; скрипты: ui_review, ux_audit | — | server.py, test_agent_task.py, test_cm08_miniapp.py (+12) | экран Mini App: HTML без данных |
| GET `/webapp/week/month` | `services/tool-api/app/pages/routes.py:85` | Mini App: routes, week_summary; скрипты: ui_review | — | server.py, test_cm08_miniapp.py, test_cm10_reports.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/week/summary` | `services/tool-api/app/pages/routes.py:80` | Mini App: month, routes, week, week_sheet, week_summary; скрипты: ui_review, ux_audit | — | server.py, test_miniapp_month_grid.py, test_miniapp_slots.py (+3) | экран Mini App: HTML без данных |

## 3. Экраны Mini App и что они зовут

«Зовёт эндпоинты» — вызовы `api()`/`fetch()` внутри вёрстки экрана; навигационные ссылки сюда не входят (они видны в колонке «Интерфейсы» у самого маршрута). Экран собран из частей (`HOME_PAGE` = тело + скрипты, `misc.*` — реэкспорт), их содержимое раскрыто по ссылкам. `tests/webapp/server.py` — стенд браузерных тестов, а не тест.

| Экран | Точка входа | Код страницы | Зовёт эндпоинты | Тест |
|---|---|---|---|---|
| `/webapp` | `services/tool-api/app/pages/routes.py:97` | `misc.PROFILE_PAGE` | `/webapp/more`, `/webapp/profile` | нет |
| `/webapp/admin/t/agent` | `services/tool-api/app/pages/routes.py:176` | `admin.AGENT_PAGE` | `/webapp/admin/agent` | нет |
| `/webapp/admin/t/archive` | `services/tool-api/app/pages/routes.py:176` | `admin.ARCHIVE_PAGE` | `/webapp/admin/unarchive` | нет |
| `/webapp/admin/t/audit` | `services/tool-api/app/pages/routes.py:176` | `admin.AUDIT_PAGE` | — | нет |
| `/webapp/admin/t/dues` | `services/tool-api/app/pages/routes.py:176` | `admin.DUES_ADMIN_PAGE` | `/webapp/admin/dues/accrue`, `/webapp/admin/tariff` | нет |
| `/webapp/admin/t/feedback` | `services/tool-api/app/pages/routes.py:176` | `admin.FEEDBACK_QUEUE_PAGE` | `/webapp/admin/feedback`, `/webapp/admin/feedback/decide`, `/webapp/admin/overview` | нет |
| `/webapp/admin/t/invites` | `services/tool-api/app/pages/routes.py:176` | `admin.INVITES_PAGE` | `/webapp/admin/invite`, `/webapp/admin/invite-revoke` | нет |
| `/webapp/admin/t/notifications` | `services/tool-api/app/pages/routes.py:176` | `admin.NOTIFICATION_DEFAULTS_PAGE` | `/webapp/admin/notifications/settings` | нет |
| `/webapp/admin/t/pending` | `services/tool-api/app/pages/routes.py:176` | `admin.PENDING_PAGE` | `/webapp/admin/approve` | нет |
| `/webapp/admin/t/people` | `services/tool-api/app/pages/routes.py:176` | `admin.PEOPLE_PAGE` | `/webapp/admin/archive`, `/webapp/admin/block`, `/webapp/admin/discipline`, `/webapp/admin/grant`, `/webapp/admin/person`, `/webapp/admin/tariff`, `/webapp/form-data` | нет |
| `/webapp/admin/t/rights` | `services/tool-api/app/pages/routes.py:176` | `admin.RIGHTS_PAGE` | `/webapp/admin/grant`, `/webapp/admin/person` | нет |
| `/webapp/broadcast` | `services/tool-api/app/pages/routes.py:132` | `misc.BROADCAST_PAGE` | `/webapp/admin/broadcast`, `/webapp/admin/broadcast/preview`, `/webapp/more` | нет |
| `/webapp/dues` | `services/tool-api/app/pages/routes.py:107` | `misc.DUES_PAGE` | `/webapp/money`, `/webapp/my/dues` | нет |
| `/webapp/feedback` | `services/tool-api/app/pages/routes.py:117` | `misc.FEEDBACK_PAGE` | `/webapp/feedback`, `/webapp/feedback/mine`, `/webapp/more` | нет |
| `/webapp/files` | `services/tool-api/app/pages/routes.py:147` | `files.FILES_PAGE` | `/webapp/admin/overview`, `/webapp/files/commit`, `/webapp/files/list`, `/webapp/files/presign`, `/webapp/files/{}/access`, `/webapp/files/{}/access/revoke`, `/webapp/folders`, `/webapp/folders/{}/delete`, `/webapp/folders/{}/rename`, `/webapp/more`, `/webapp/tags/pending`, `/webapp/tags/{}/approve` | нет |
| `/webapp/folder` | `services/tool-api/app/pages/routes.py:157` | `files.FOLDER_PAGE` | `/webapp/admin/overview`, `/webapp/files`, `/webapp/folder/{}/files`, `/webapp/folder/{}/order`, `/webapp/folder/{}/zip` | нет |
| `/webapp/home` | `services/tool-api/app/pages/routes.py:65` | `home.HOME_PAGE` | `/webapp/home/data` | нет |
| `/webapp/money` | `services/tool-api/app/pages/routes.py:90` | `money.MONEY_PAGE` | `/webapp/admin/overview`, `/webapp/home/data`, `/webapp/my/dues` | нет |
| `/webapp/more` | `services/tool-api/app/pages/routes.py:70` | `home.MORE_PAGE` | `/webapp/home/data` | нет |
| `/webapp/notes` | `services/tool-api/app/pages/routes.py:152` | `notes.NOTES_PAGE` | `/webapp/admin/overview`, `/webapp/more`, `/webapp/notes/list`, `/webapp/notes/save` | нет |
| `/webapp/notifications` | `services/tool-api/app/pages/routes.py:112` | `misc.NOTIFICATIONS_PAGE` | `/webapp/more`, `/webapp/notifications/settings` | нет |
| `/webapp/person` | `services/tool-api/app/pages/routes.py:162` | `person.PERSON_PAGE` | `/webapp/home`, `/webapp/person/info`, `/webapp/person/tariff` | нет |
| `/webapp/reconcile` | `services/tool-api/app/pages/routes.py:127` | `misc.RECONCILE_PAGE` | `/webapp/admin/reconcile`, `/webapp/more`, `/webapp/nav` | нет |
| `/webapp/services` | `services/tool-api/app/pages/routes.py:142` | `services.SERVICES_PAGE` | `/webapp/admin/services`, `/webapp/more` | нет |
| `/webapp/slots` | `services/tool-api/app/pages/routes.py:122` | `misc.SLOTS_PAGE` | `/webapp/assign`, `/webapp/preview`, `/webapp/slots/find`, `/webapp/slots/people`, `/webapp/week` | нет |
| `/webapp/tx` | `services/tool-api/app/pages/routes.py:102` | `money.TX_PAGE` | `/webapp/categories`, `/webapp/home/data`, `/webapp/money`, `/webapp/tx/save` | нет |
| `/webapp/ui` | `services/tool-api/app/pages/routes.py:137` | `kit.UI_KIT_PAGE` | — | нет |
| `/webapp/week` | `services/tool-api/app/pages/routes.py:75` | `week.WEEK_PAGE` | `/webapp/availability/clear`, `/webapp/availability/sheet/load`, `/webapp/availability/sheet/preview`, `/webapp/availability/template/apply`, `/webapp/availability/template/from-week`, `/webapp/availability/week`, `/webapp/nav` | нет |
| `/webapp/week/month` | `services/tool-api/app/pages/routes.py:85` | `week.MONTH_PAGE` | `/webapp/month-grid`, `/webapp/week/summary` | нет |
| `/webapp/week/summary` | `services/tool-api/app/pages/routes.py:80` | `week.SUMMARY_PAGE` | `/webapp/grid` | нет |

## 4. Telegram-гейтвей: команды, кнопки, текстовые ветки

Право в чате — то, что проверяет сам обработчик (`core.has(caps, …)`, `in caps`). «—» значит, что собственной проверки нет: окончательное решение принимает tool-api по праву эндпоинта из колонки «Зовёт эндпоинты». Вызовы через общие помощники (`send_invite`, `panel_person` и подобные) видны в строке помощника, а не вызывающего.

| Действие | Триггер | Точка входа | Право в чате | Тест | Зовёт эндпоинты |
|---|---|---|---|---|---|
| кнопка `panel_archive` | F.data == 'panel:archive' | `services/tg-gateway/app/routers/admin.py:169` | — | test_gateway_chat_parity.py | — |
| кнопка `panel_person_archive` | F.data.startswith('panel:archive:') | `services/tg-gateway/app/routers/admin.py:178` | — | test_gateway_chat_parity.py | `/admin/archive` |
| кнопка `panel_person_unarchive` | F.data.startswith('panel:unarchive:') | `services/tg-gateway/app/routers/admin.py:202` | — | test_gateway_chat_parity.py | `/admin/unarchive` |
| кнопка `panel_person_block` | F.data.startswith('panel:block:') | `services/tg-gateway/app/routers/admin.py:255` | — | test_gateway_chat_parity.py | — |
| кнопка `panel_person_unblock` | F.data.startswith('panel:unblock:') | `services/tg-gateway/app/routers/admin.py:261` | — | test_gateway_chat_parity.py | — |
| кнопка `panel_person` | F.data.startswith('panel:person:') | `services/tg-gateway/app/routers/admin.py:267` | admin.panel, budget.write, participants.manage | test_gateway_chat_parity.py | `/tools/participants/{}/card`, `/webapp/person` |
| кнопка `panel_person_roles` | F.data.startswith('panel:proles:') | `services/tg-gateway/app/routers/admin.py:309` | — | test_gateway_chat_parity.py, test_gateway_routing.py | `/tools/participants/{}/card` |
| кнопка `panel_person_role_toggle` | F.data.startswith('panel:role:') | `services/tg-gateway/app/routers/admin.py:338` | — | нет | `/admin/grant` |
| кнопка `panel_person_master` | F.data.startswith('panel:mroles:') | `services/tg-gateway/app/routers/admin.py:369` | — | test_gateway_chat_parity.py | `/tools/disciplines` |
| кнопка `panel_person_master_link` | F.data.startswith('panel:mrel:') | `services/tg-gateway/app/routers/admin.py:407` | — | test_gateway_chat_parity.py | `/admin/participant/discipline` |
| кнопка `panel_person_master_grant` | F.data.startswith('panel:mgrant:') | `services/tg-gateway/app/routers/admin.py:427` | — | test_gateway_chat_parity.py | `/admin/grant` |
| кнопка `panel_person_caps` | F.data.startswith('panel:caps:') | `services/tg-gateway/app/routers/admin.py:452` | — | test_gateway_routing.py | `/admin/capabilities`, `/tools/participants/{}/card` |
| кнопка `panel_person_cap_toggle` | F.data.startswith('panel:cap:') | `services/tg-gateway/app/routers/admin.py:481` | — | нет | `/admin/grant` |
| кнопка `panel_person_tariff` | F.data.startswith('panel:tariff:') | `services/tg-gateway/app/routers/admin.py:499` | — | test_gateway_chat_parity.py, test_gateway_routing.py | `/tools/participants/{}/card` |
| сообщение `panel_tariff_save` | PanelEdit.tariff, F.text | `services/tg-gateway/app/routers/admin.py:527` | — | нет | `/tools/participants/{}/dues-tariff` |
| кнопка `panel_pending` | F.data == 'panel:pending' | `services/tg-gateway/app/routers/admin.py:551` | — | test_gateway_routing.py | `/admin/pending` |
| кнопка `panel_rights` | F.data == 'panel:rights' | `services/tg-gateway/app/routers/admin.py:575` | — | test_gateway_chat_parity.py, test_gateway_routing.py | `/admin/capabilities`, `/admin/grant` |
| кнопка `panel_invite` | F.data == 'panel:invite' | `services/tg-gateway/app/routers/admin.py:598` | participants.manage, registration.code.issue | test_admin_panel_wiring.py, test_gateway_invite_copy.py (+1) | — |
| кнопка `invite_issue` | F.data.startswith('inv:role:') | `services/tg-gateway/app/routers/admin.py:615` | — | test_admin_panel_wiring.py | `/admin/invite` |
| кнопка `panel_codes` | F.data == 'panel:codes' | `services/tg-gateway/app/routers/admin.py:673` | — | test_gateway_chat_parity.py | — |
| кнопка `invite_revoke` | F.data.startswith('inv:revoke:') | `services/tg-gateway/app/routers/admin.py:687` | — | test_gateway_chat_parity.py | `/admin/invite/revoke` |
| кнопка `panel_money` | F.data.startswith('panel:money') | `services/tg-gateway/app/routers/admin.py:706` | budget.write | test_panel_money_periods.py | `/tools/budget/recent` |
| кнопка `reconcile_apply` | F.data == 'panel:reconcile_apply' | `services/tg-gateway/app/routers/admin.py:747` | — | нет | `/admin/reconcile` |
| кнопка `panel_sheet_retry` | F.data == 'panel:sheet_retry' | `services/tg-gateway/app/routers/admin.py:756` | — | test_gateway_routing.py, test_gateway_sheet_retry.py | `/tools/budget/sheet-retry` |
| кнопка `panel_feedback` | F.data == 'panel:feedback' | `services/tg-gateway/app/routers/admin.py:767` | — | test_admin_panel_wiring.py, test_panel_money_periods.py | `/tools/feedback` |
| кнопка `panel_audit` | F.data == 'panel:audit' | `services/tg-gateway/app/routers/admin.py:810` | — | нет | `/tools/audit/recent` |
| кнопка `panel_health` | F.data == 'panel:health' | `services/tg-gateway/app/routers/admin.py:832` | — | нет | `/health` |
| кнопка `admin_decision` | F.data.startswith('adm:') | `services/tg-gateway/app/routers/admin.py:843` | — | нет | `/admin/approve` |
| кнопка `panel_reconcile` | F.data == 'panel:reconcile' | `services/tg-gateway/app/routers/admin.py:860` | — | test_gateway_registration.py | `/admin/reconcile` |
| кнопка `reconcile_apply_panel` | F.data == 'rec:apply' | `services/tg-gateway/app/routers/admin.py:873` | — | test_gateway_registration.py | `/admin/reconcile` |
| кнопка `panel_back` | F.data == 'panel:root' | `services/tg-gateway/app/routers/admin.py:93` | — | test_gateway_chat_parity.py, test_gateway_invite_copy.py (+1) | — |
| кнопка `panel_people` | F.data == 'panel:people' | `services/tg-gateway/app/routers/admin.py:99` | — | test_gateway_chat_parity.py, test_gateway_menu_rights.py (+1) | `/tools/participants` |
| кнопка `agent_action_cb` | F.data.startswith('ag:') | `services/tg-gateway/app/routers/agent.py:23` | — | нет | `/agent/actions/{}/{}` |
| сообщение `assign_start` | F.text.regexp(ASSIGN_Q_PATTERN, flags=re.IGNORECASE) | `services/tg-gateway/app/routers/assign.py:173` | — | нет | — |
| кнопка `draft_cancel` | F.data == 'evdraft:no' | `services/tg-gateway/app/routers/assign.py:210` | — | test_gateway_assign_dialog.py | — |
| кнопка `assign_pick` | F.data.startswith('evpick:') | `services/tg-gateway/app/routers/assign.py:218` | — | нет | `/tools/events/choose` |
| кнопка `assign_confirm` | F.data.startswith('evok:') | `services/tg-gateway/app/routers/assign.py:238` | — | test_event_chat_card.py | `/tools/events/assign` |
| кнопка `assign_cancel` | F.data.startswith('evno:') | `services/tg-gateway/app/routers/assign.py:267` | — | нет | — |
| кнопка `slots_more` | F.data.startswith('slots:') | `services/tg-gateway/app/routers/availability.py:147` | — | нет | — |
| кнопка `av_day` | F.data.startswith('av:day:') | `services/tg-gateway/app/routers/availability.py:257` | — | test_gateway_avail_parity.py | — |
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
| сообщение `cmd_files` | Command('files', texts.TITLE_FILES) | `services/tg-gateway/app/routers/files.py:256` | — | test_action_registry.py, test_files_delete.py (+2) | `/files` |
| кнопка `file_send` | F.data.startswith('fl:') | `services/tg-gateway/app/routers/files.py:278` | admin.panel | test_gateway_files_rights.py | `/files/{}`, `/files/{}/content`, `/files/{}/link` |
| сообщение `cmd_folders` | Command('folders', 'папки') | `services/tg-gateway/app/routers/files.py:329` | — | test_action_registry.py, test_files_actions_screen.py (+2) | `/folders` |
| сообщение `cmd_mkdir` | Command('mkdir', 'папка') | `services/tg-gateway/app/routers/files.py:356` | — | test_gateway_files_rights.py | `/folders` |
| сообщение `cmd_rename` | Command('rename', 'переименовать') | `services/tg-gateway/app/routers/files.py:399` | — | test_gateway_files_rights.py | `/folders/{}/rename` |
| сообщение `cmd_rmfolder` | Command('rmfolder', 'удалить-папку') | `services/tg-gateway/app/routers/files.py:418` | — | test_gateway_files_rights.py | `/folders/{}/delete` |
| сообщение `cmd_order` | Command('order', 'порядок') | `services/tg-gateway/app/routers/files.py:438` | — | test_gateway_files_rights.py | `/folders/{}/order` |
| сообщение `cmd_access` | Command('access', 'доступ') | `services/tg-gateway/app/routers/files.py:464` | — | test_gateway_files_rights.py | `/admin/files/{}/access` |
| сообщение `cmd_revoke` | Command('revoke', 'отозвать') | `services/tg-gateway/app/routers/files.py:492` | — | test_gateway_files_rights.py | `/files/{}/access/revoke` |
| сообщение `cmd_zip` | Command('zip', 'архив') | `services/tg-gateway/app/routers/files.py:537` | — | test_gateway_files_rights.py, test_gateway_files_zip.py (+1) | `/folders/{}/zip`, `/webapp/folder` |
| сообщение `cmd_search` | Command('search', 'найти') | `services/tg-gateway/app/routers/files.py:574` | — | test_gateway_files_rights.py | `/files` |
| сообщение `cmd_tags` | Command('tags', 'теги') | `services/tg-gateway/app/routers/files.py:602` | — | test_action_registry.py, test_files_actions_screen.py (+2) | `/tags` |
| сообщение `cmd_tag` | Command('tag', 'тег') | `services/tg-gateway/app/routers/files.py:621` | — | test_gateway_files_rights.py | `/files/{}/tags`, `/tags` |
| сообщение `cmd_tag_ok` | Command('tag-ok', 'тег-ок') | `services/tg-gateway/app/routers/files.py:664` | — | test_gateway_files_rights.py | `/tags/{}/approve` |
| сообщение `on_video` | F.video \| F.video_note \| F.animation | `services/tg-gateway/app/routers/files.py:80` | — | нет | — |
| сообщение `on_file` | F.document \| F.photo \| F.audio \| F.voice | `services/tg-gateway/app/routers/files.py:90` | — | нет | — |
| сообщение `cmd_intro` | Command('intro') | `services/tg-gateway/app/routers/intro.py:107` | — | test_intro.py | — |
| кнопка `intro_save` | F.data == 'intro:save' | `services/tg-gateway/app/routers/intro.py:112` | — | нет | `/tools/profile/me` |
| кнопка `intro_cancel` | F.data == 'intro:cancel' | `services/tg-gateway/app/routers/intro.py:132` | — | нет | — |
| сообщение `intro_name` | Intro.name, F.text | `services/tg-gateway/app/routers/intro.py:139` | — | нет | — |
| сообщение `intro_alias` | Intro.alias, F.text | `services/tg-gateway/app/routers/intro.py:144` | — | нет | — |
| сообщение `intro_phone` | Intro.phone, F.text | `services/tg-gateway/app/routers/intro.py:149` | — | нет | — |
| сообщение `intro_birthday` | Intro.birthday, F.text | `services/tg-gateway/app/routers/intro.py:154` | — | нет | — |
| сообщение `intro_travel` | Intro.travel, F.text | `services/tg-gateway/app/routers/intro.py:159` | — | нет | — |
| сообщение `intro_plays` | Intro.plays, F.text | `services/tg-gateway/app/routers/intro.py:164` | — | нет | — |
| сообщение `intro_roles` | Intro.roles, F.text | `services/tg-gateway/app/routers/intro.py:169` | — | нет | — |
| сообщение `intro_trainings` | Intro.trainings, F.text | `services/tg-gateway/app/routers/intro.py:174` | — | нет | — |
| сообщение `intro_manifest` | Intro.manifest, F.text | `services/tg-gateway/app/routers/intro.py:179` | — | нет | — |
| кнопка `intro_skip` | F.data == 'intro:skip' | `services/tg-gateway/app/routers/intro.py:184` | — | нет | — |
| сообщение `start` | CommandStart() | `services/tg-gateway/app/routers/menu.py:120` | — | нет | `/admin/bootstrap`, `/tools/participants/me` |
| сообщение `on_menu_button` | StateFilter(None, AssignDraft.await_kind, AssignDraft.await_date, AssignDraft.await_time), F.text.in_(keyboards.MENU_SURFACE) | `services/tg-gateway/app/routers/menu.py:340` | — | нет | — |
| кнопка `menu_item_cb` | F.data.startswith('mi:') | `services/tg-gateway/app/routers/menu.py:347` | — | test_agent_features.py, test_gateway_menu_rights.py | — |
| сообщение `on_dialog_control` | F.text.in_(keyboards.DIALOG_CONTROLS) | `services/tg-gateway/app/routers/menu.py:583` | — | нет | — |
| сообщение `task_frame_text` | TaskFrame.await_text, F.text, ~F.text.startswith('/') | `services/tg-gateway/app/routers/menu.py:588` | — | нет | — |
| сообщение `on_text` | StateFilter(None, Reg.await_code, AssignDraft.await_kind, AssignDraft.await_date, AssignDraft.await_time), F.text & ~F.text.startswith('/') | `services/tg-gateway/app/routers/menu.py:623` | budget.write | нет | `/tools/assistant/ask`, `/tools/participants/check_code` |
| сообщение `reg_filter` | Reg.choose, F.text, ~F.text.startswith('/') | `services/tg-gateway/app/routers/menu.py:788` | — | нет | `/tools/participants/search` |
| кнопка `reg_pick` | F.data.startswith('reg:pick:') | `services/tg-gateway/app/routers/menu.py:817` | — | test_intro.py | `/tools/participants/attach` |
| кнопка `reg_none` | F.data == 'reg:none' | `services/tg-gateway/app/routers/menu.py:840` | — | нет | — |
| сообщение `reg_name` | Reg.await_name, F.text | `services/tg-gateway/app/routers/menu.py:847` | — | нет | `/tools/participants/register` |
| сообщение `cmd_help` | Command('help') | `services/tg-gateway/app/routers/menu.py:881` | — | нет | — |
| сообщение `menu_cmd` | Command('menu') | `services/tg-gateway/app/routers/menu.py:887` | — | test_gateway_assign_dialog.py | — |
| сообщение `cmd_quiet` | lambda message: parsers.command_name(message.text) in QUIET_COMMANDS | `services/tg-gateway/app/routers/menu.py:955` | — | нет | — |
| кнопка `quiet_action` | F.data.startswith('quiet:') | `services/tg-gateway/app/routers/menu.py:984` | — | test_gateway_quiet.py | — |
| сообщение `cmd_notes` | Command('заметки', 'notes') | `services/tg-gateway/app/routers/notes.py:104` | — | нет | `/webapp/notes` |
| кнопка `note_confirm` | F.data.startswith('note:') | `services/tg-gateway/app/routers/notes.py:112` | notes.write | test_gateway_notes.py | `/tools/notes` |
| сообщение `cmd_note` | Command('заметка', 'note') | `services/tg-gateway/app/routers/notes.py:98` | — | test_gateway_command_hints.py, test_gateway_notes.py | — |
| кнопка `my_dues` | F.data == 'sar:dues' | `services/tg-gateway/app/routers/profile.py:114` | — | нет | `/tools/dues/me` |
| сообщение `cmd_dues` | Command('dues') | `services/tg-gateway/app/routers/profile.py:55` | — | нет | `/tools/dues/me` |
| сообщение `profile_cmd` | Command('profile') | `services/tg-gateway/app/routers/profile.py:64` | — | нет | `/tools/profile/me`, `/webapp` |

Ветки ответа на свободный текст (`app/routing.py`, порядок применения):

| Правило | Роль | Что ловит | Точка входа |
|---|---|---|---|
| `about_me` | вопрос | ^(кто я\|кто я такой\|кто я такая\|мой профиль\|мои роли\|моя роль\|кто я в боте) | `services/tg-gateway/app/routing.py:59` |
| `list_people` | вопрос | (список.*(участник\|людей\|пользовател)\|кто\s+(зарегистр\|в\s+системе\|у\s+нас\s+в\s+боте)\|зарегистрирован\|состав\s+(студи\|т | `services/tg-gateway/app/routing.py:60` |
| `howto` | вопрос | ^(как\|где\|чем\|почему\|зачем\|можно\s+ли\|подскажи\|объясни\|научи)\b | `services/tg-gateway/app/routing.py:61` |
| `slots` | вопрос | (окн[оа]\b\|когда\s+(все\s+)?свободн\|найди\s+(время\|окно)\|общее\s+время\|когда\s+можно\s+собрат\|свободные\s+часы) | `services/tg-gateway/app/routing.py:62` |
| `money` | вопрос | (сколько\s+(у\s+нас\s+)?(денег\|средств\|на\s+счет\|осталось)\|какой\s+баланс\|баланс\s+студи\|сколько\s+потратили\|сколько\s+з | `services/tg-gateway/app/routing.py:63` |
| `schedule` | вопрос | (расписан\|что\s+у\s+меня\s+(сегодня\|завтра\|послезавтра\|на\s+неделе)\|что\s+(у\s+нас\s+)?(на\|в)\s+(неделе\|пн\|вт\|ср\|чт\|пт\|с | `services/tg-gateway/app/routing.py:64` |
| `availability_q` | вопрос | (кто\s+((сегодня\|завтра\|послезавтра\|сейчас)\s+)?(занят\|свободен)\|покажи\s+занятост\|занятость\s+на\s+(сегодня\|завтра)) | `services/tg-gateway/app/routing.py:65` |
| `availability` | ввод данных | занят\|свобод\|могу\|не могу\|не смогу\|удобн\|работаю | `services/tg-gateway/app/routing.py:66` |
| `assign` | ввод данных | (назначь\|назначить\|запланируй\|поставь\s+(репетици\|показ\|тренинг\|аренд\|встреч)) | `services/tg-gateway/app/routing.py:68` |
| `budget` | ввод данных | расход\|потрат\|оплат\|заплат\|купил\|отдал\|приход\|поступ\|взнос\|перевел | `services/tg-gateway/app/routing.py:69` |

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
| GET `/health` | `services/tool-api/app/main.py:244` | чат: panel_health, today_local; скрипты: _declared_routes, check_availability, check_health, check_services, fetch_health, health, health_agent, install_launchd, neighbor_counts, restore_dump, start_services (по шаблону) | — | test_arch_drift.py, test_ci_images.py, test_cm01_login.py (+2) | — |
| POST `/tools/participants/attach` | `services/tool-api/app/api/people.py:639` | чат: reg_pick | — | test_intro.py, test_registration.py | — |
| POST `/tools/participants/check_code` | `services/tool-api/app/api/people.py:583` | чат: on_text | — | test_admin_invites.py, test_gateway_registration.py, test_registration.py (+1) | — |
| POST `/tools/participants/register` | `services/tool-api/app/api/people.py:43` | чат: reg_name | — | test_cm01_login.py, test_registration.py | — |
| GET `/webapp` | `services/tool-api/app/pages/routes.py:97` | Mini App: home, routes; чат: profile_cmd, run_menu_action; скрипты: neighbor_counts, restore_dump, ui_review, ux_audit (по шаблону) | — | server.py, test_agent_features.py, test_miniapp_visual.py (+4) | экран Mini App: HTML без данных |
| GET `/webapp/admin` | `services/tool-api/app/pages/routes.py:169` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_static_contract.py, test_webapp_admin.py | — |
| GET `/webapp/admin/t/agent` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: intent_report, ui_review, ux_audit | — | server.py, test_agent_observability.py, test_miniapp_hub.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/archive` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_archive_rights.py, test_miniapp_hub.py (+3) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/audit` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_miniapp_audit_rights.py, test_miniapp_hub.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/budget` | `services/tool-api/app/pages/routes.py:181` | Mini App: routes (по шаблону) | — | server.py, test_miniapp_hub.py | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/dues` | `services/tool-api/app/pages/routes.py:176` | Mini App: admin_people, routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_ui_review.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/feedback` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_feedback_open_only.py, test_miniapp_feedback_rights.py (+2) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/invites` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_miniapp_invites_rights.py (+2) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/notifications` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_ui_review.py (+1) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/pending` | `services/tool-api/app/pages/routes.py:176` | Mini App: home, routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_pending_rights.py (+2) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/people` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_js_behavior.py (+4) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/admin/t/rights` | `services/tool-api/app/pages/routes.py:176` | Mini App: routes (по шаблону); скрипты: ui_review | — | server.py, test_miniapp_hub.py, test_miniapp_rights_screen.py (+2) | раздел из реестра app/webapp.py:SECTIONS |
| GET `/webapp/assign` | `services/tool-api/app/pages/routes.py:217` | Mini App: misc_slots, routes; скрипты: ui_review | — | server.py, test_agent_task.py, test_gateway_agent_plan.py (+1) | — |
| POST `/webapp/auth` | `services/tool-api/app/api/webapp_api.py:35` | Mini App: webapp-shell.js; скрипты: issue_tokens | — | server.py, test_admin_invites.py, test_admin_people_actions.py (+30) | — |
| POST `/webapp/boot` | `services/tool-api/app/api/webapp_api.py:87` | Mini App: layout, webapp-shell.js | — | test_miniapp_net_guard.py, test_webapp_boot.py | — |
| GET `/webapp/broadcast` | `services/tool-api/app/pages/routes.py:132` | Mini App: routes; скрипты: ui_review, ux_audit | — | server.py, test_broadcast_rights.py, test_miniapp_broadcast.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/days` | `services/tool-api/app/pages/routes.py:202` | Mini App: routes; чат: _buttons_with_week, send_menu; скрипты: ui_review | — | test_miniapp_system.py, test_static_contract.py, test_webapp_merge_deps.py (+1) | — |
| GET `/webapp/dues` | `services/tool-api/app/pages/routes.py:107` | Mini App: kit, routes; скрипты: ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_miniapp_visual.py | экран Mini App: HTML без данных |
| GET `/webapp/feedback` | `services/tool-api/app/pages/routes.py:117` | Mini App: home, misc_feedback, routes; скрипты: ui_review, ux_audit | — | server.py, test_feedback.py, test_miniapp_js_behavior.py (+1) | экран Mini App: HTML без данных |
| GET `/webapp/files` | `services/tool-api/app/pages/routes.py:147` | Mini App: files_actions, files_folder, home, routes; скрипты: ui_review | — | server.py, test_agent_task.py, test_files_studio_roles.py (+7) | экран Mini App: HTML без данных |
| GET `/webapp/folder` | `services/tool-api/app/pages/routes.py:157` | Mini App: files, files_actions, routes; чат: _zip_refusal_text, cmd_zip; скрипты: ui_review | — | server.py, test_miniapp_shell_contract.py, test_miniapp_visual.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/home` | `services/tool-api/app/pages/routes.py:65` | Mini App: kit, layout, person, routes, webapp-shell.js; чат: _show_open_card, ensure_menu_button, main, with_home; скрипты: ui_review, ux_audit | — | server.py, test_agent_task.py, test_miniapp_home_loading.py (+7) | экран Mini App: HTML без данных |
| GET `/webapp/money` | `services/tool-api/app/pages/routes.py:90` | Mini App: layout, misc_dues, money_script, money_tx, money_tx_script, routes; скрипты: ui_review, ux_audit | — | server.py, test_cm08_miniapp.py, test_cm10_reports.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/more` | `services/tool-api/app/pages/routes.py:70` | Mini App: admin_core, files, home, home_loading, kit, layout, misc_broadcast, misc_feedback, misc_notifications, misc_profile, misc_reconcile, notes, routes, services, webapp-shell.js; скрипты: ui_review, ux_audit | — | server.py, test_miniapp_hub.py, test_webapp_assets.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/my/sheet` | `services/tool-api/app/pages/routes.py:207` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_webapp_merge_deps.py | — |
| GET `/webapp/notes` | `services/tool-api/app/pages/routes.py:152` | Mini App: routes; чат: cmd_notes, note_start, notes; скрипты: ui_review | — | server.py, test_agent_task.py, test_miniapp_notes.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/notifications` | `services/tool-api/app/pages/routes.py:112` | Mini App: admin_notifications, home, routes; чат: quiet_text; скрипты: ui_review, ux_audit | — | server.py, test_gateway_quiet.py, test_miniapp_hub.py | экран Mini App: HTML без данных |
| GET `/webapp/people` | `services/tool-api/app/pages/routes.py:212` | Mini App: routes; скрипты: ui_review | — | test_agent_task.py | — |
| GET `/webapp/person` | `services/tool-api/app/pages/routes.py:162` | Mini App: person, routes; чат: panel_person; скрипты: ui_review | — | server.py, test_miniapp_js_behavior.py, test_miniapp_system.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/reconcile` | `services/tool-api/app/pages/routes.py:127` | Mini App: layout, routes; скрипты: ui_review, ux_audit | — | server.py, test_miniapp_reconcile_rights.py, test_webapp_layout.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/schedule` | `services/tool-api/app/pages/routes.py:222` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_ui_review.py | — |
| GET `/webapp/search` | `services/tool-api/app/pages/routes.py:197` | Mini App: routes; скрипты: ui_review | — | test_miniapp_system.py, test_webapp_search.py | — |
| GET `/webapp/services` | `services/tool-api/app/pages/routes.py:142` | Mini App: routes; скрипты: ui_review, ux_audit | — | server.py, test_webapp_screen_rights.py, test_webapp_sections.py (+1) | экран Mini App: HTML без данных |
| GET `/webapp/shell.js` | `services/tool-api/app/api/webapp_api.py:112` | Mini App: layout | — | server.py, test_routes_contract.py, test_static_contract.py (+4) | — |
| GET `/webapp/slots` | `services/tool-api/app/pages/routes.py:122` | Mini App: misc_slots_denied, routes, week_summary; чат: answer_slots, availability, run_menu_action; скрипты: ui_review, ux_audit | — | server.py, test_agent_task.py, test_gateway_slots_entry.py (+3) | экран Mini App: HTML без данных |
| GET `/webapp/tg.js` | `services/tool-api/app/api/webapp_api.py:60` | Mini App: layout | — | server.py, test_cm08_miniapp.py, test_webapp_assets.py (+1) | — |
| GET `/webapp/tx` | `services/tool-api/app/pages/routes.py:102` | Mini App: home_loading, money, money_script, routes; чат: run_menu_action; скрипты: ui_review, ux_audit | — | server.py, test_agent_task.py, test_miniapp_js_behavior.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/ui` | `services/tool-api/app/pages/routes.py:137` | Mini App: kit, routes; скрипты: ui_review, ux_audit | — | server.py, test_miniapp_home_loading.py, test_miniapp_hub.py | экран Mini App: HTML без данных |
| GET `/webapp/ui.css` | `services/tool-api/app/api/webapp_api.py:125` | Mini App: layout, webapp-shell.js | — | server.py, test_cm11_degradation.py, test_routes_contract.py (+3) | — |
| GET `/webapp/week` | `services/tool-api/app/pages/routes.py:75` | Mini App: home_loading, kit, layout, misc_slots, routes, week_sheet, week_summary; скрипты: ui_review, ux_audit | — | server.py, test_agent_task.py, test_cm08_miniapp.py (+12) | экран Mini App: HTML без данных |
| GET `/webapp/week/month` | `services/tool-api/app/pages/routes.py:85` | Mini App: routes, week_summary; скрипты: ui_review | — | server.py, test_cm08_miniapp.py, test_cm10_reports.py (+2) | экран Mini App: HTML без данных |
| GET `/webapp/week/summary` | `services/tool-api/app/pages/routes.py:80` | Mini App: month, routes, week, week_sheet, week_summary; скрипты: ui_review, ux_audit | — | server.py, test_miniapp_month_grid.py, test_miniapp_slots.py (+3) | экран Mini App: HTML без данных |

## 7. Права, проверенные в теле обработчика

Три пути проверки права — не считая зависимости `core.require(…)` в §2: `core._webapp_require(who, "…")` у Mini App (7a), служебный Bearer-токен на входе (7b) и ручная проверка `"*" in caps` (7c).

### 7a. `core._webapp_require(who, "…")` — Mini App

Обработчик получает участника зависимостью `core.webapp_actor` (сессия), а возможность проверяет сам: `_webapp_require` — это `require()` для прямого вызова (`app/core.py:720-733`). В колонке «Право» §2 такая возможность приписана к сессии.

| Действие | Точка входа | Право | Сессия | Тест |
|---|---|---|---|---|
| GET `/webapp/admin/agent` | `services/tool-api/app/api/admin.py:1102` | admin.panel | сессия Mini App | server.py, test_agent_observability.py |
| POST `/webapp/admin/approve` | `services/tool-api/app/api/admin.py:838` | registration.approve | сессия Mini App | test_webapp_admin_actions.py, test_webapp_admin_pages.py |
| POST `/webapp/admin/archive` | `services/tool-api/app/api/admin.py:1142` | participants.manage | сессия Mini App | test_admin_archive_chat.py, test_admin_people_actions.py, test_miniapp_archive_rights.py (+2) |
| POST `/webapp/admin/block` | `services/tool-api/app/api/admin.py:1270` | participants.manage | сессия Mini App | test_admin_people_actions.py, test_webapp_pages_js.py |
| POST `/webapp/admin/broadcast` | `services/tool-api/app/api/admin.py:1364` | notify.send | сессия Mini App | server.py, test_broadcast_rights.py, test_cm05_notifications.py |
| GET `/webapp/admin/broadcast/preview` | `services/tool-api/app/api/admin.py:1346` | notify.send | сессия Mini App | server.py, test_broadcast_rights.py, test_cm05_notifications.py (+1) |
| POST `/webapp/admin/discipline` | `services/tool-api/app/api/admin.py:852` | participants.manage | сессия Mini App | нет |
| GET `/webapp/admin/dues` | `services/tool-api/app/api/finance.py:110` | budget.write | сессия Mini App | server.py, test_dues_admin_ui.py, test_webapp_admin_pages.py |
| POST `/webapp/admin/dues/accrue` | `services/tool-api/app/api/finance.py:174` | budget.write | сессия Mini App | server.py, test_dues_admin_ui.py, test_webapp_admin_pages.py |
| GET `/webapp/admin/feedback` | `services/tool-api/app/api/admin.py:925` | feedback.triage | сессия Mini App | server.py |
| POST `/webapp/admin/feedback/decide` | `services/tool-api/app/api/admin.py:937` | feedback.triage | сессия Mini App | test_feedback_open_only.py, test_webapp_merge_deps.py |
| POST `/webapp/admin/grant` | `services/tool-api/app/api/admin.py:845` | participants.manage | сессия Mini App | test_webapp_admin_actions.py, test_webapp_admin_pages.py, test_webapp_pages_js.py |
| POST `/webapp/admin/invite` | `services/tool-api/app/api/admin.py:908` | registration.code.issue | сессия Mini App | test_admin_invites.py, test_webapp_admin_pagination.py |
| POST `/webapp/admin/invite-revoke` | `services/tool-api/app/api/admin.py:918` | registration.code.issue | сессия Mini App | test_admin_invites.py, test_webapp_admin_pages.py |
| GET `/webapp/admin/invites` | `services/tool-api/app/api/admin.py:894` | registration.code.issue | сессия Mini App | test_admin_invites.py |
| GET `/webapp/admin/notifications/settings` | `services/tool-api/app/api/admin.py:1305` | admin.panel | сессия Mini App | server.py, test_notification_defaults.py, test_webapp_admin_pages.py |
| POST `/webapp/admin/notifications/settings` | `services/tool-api/app/api/admin.py:1315` | admin.panel | сессия Mini App | server.py, test_notification_defaults.py, test_webapp_admin_pages.py |
| POST `/webapp/admin/person` | `services/tool-api/app/api/admin.py:885` | participants.view | сессия Mini App | test_participants_card.py, test_webapp_admin_pages.py |
| POST `/webapp/admin/reconcile` | `services/tool-api/app/api/admin.py:264` | budget.import | сессия Mini App | server.py |
| GET `/webapp/admin/services` | `services/tool-api/app/api/admin.py:1053` | admin.panel | сессия Mini App | server.py, test_cm08_miniapp.py, test_cm11_degradation.py |
| POST `/webapp/admin/sheet-retry` | `services/tool-api/app/api/admin.py:868` | budget.write | сессия Mini App | test_webapp_sheet_retry.py |
| POST `/webapp/admin/tariff` | `services/tool-api/app/api/admin.py:860` | budget.write | сессия Mini App | test_dues_admin_ui.py, test_webapp_admin_actions.py, test_webapp_admin_pages.py (+1) |
| POST `/webapp/admin/unarchive` | `services/tool-api/app/api/admin.py:1194` | participants.manage | сессия Mini App | test_admin_archive_chat.py, test_admin_people_actions.py, test_miniapp_archive_rights.py (+1) |
| POST `/webapp/feedback` | `services/tool-api/app/api/admin.py:731` | feedback.send | сессия Mini App | server.py, test_feedback.py, test_miniapp_js_behavior.py (+1) |
| POST `/webapp/files/commit` | `services/tool-api/app/api/files.py:326` | files.access | сессия Mini App | test_files_delete.py, test_webapp_files.py, test_webapp_files_pages.py |
| GET `/webapp/files/list` | `services/tool-api/app/api/files.py:282` | files.access | сессия Mini App | server.py, test_files_delete.py, test_files_studio_roles.py (+2) |
| POST `/webapp/files/presign` | `services/tool-api/app/api/files.py:295` | files.access | сессия Mini App | test_files_delete.py, test_webapp_files.py, test_webapp_files_pages.py (+1) |
| DELETE `/webapp/files/{file_id}` | `services/tool-api/app/api/files.py:370` | files.access | сессия Mini App | server.py, test_files_delete.py, test_files_studio_roles.py (+3) |
| GET `/webapp/files/{file_id}/access` | `services/tool-api/app/api/files.py:722` | files.access | сессия Mini App | server.py, test_files_actions_screen.py, test_files_studio_roles.py |
| POST `/webapp/files/{file_id}/access` | `services/tool-api/app/api/files.py:697` | files.access | сессия Mini App | server.py, test_files_actions_screen.py, test_files_studio_roles.py |
| POST `/webapp/files/{file_id}/access/revoke` | `services/tool-api/app/api/files.py:711` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| GET `/webapp/folder/{folder_id}/files` | `services/tool-api/app/api/files.py:502` | files.access | сессия Mini App | server.py, test_folder_order.py |
| POST `/webapp/folder/{folder_id}/order` | `services/tool-api/app/api/files.py:518` | files.access | сессия Mini App | test_files_actions_screen.py, test_folder_order.py |
| GET `/webapp/folder/{folder_id}/zip` | `services/tool-api/app/api/files.py:531` | files.access | сессия Mini App | нет |
| GET `/webapp/folders` | `services/tool-api/app/api/files.py:652` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| POST `/webapp/folders` | `services/tool-api/app/api/files.py:662` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| POST `/webapp/folders/{folder_id}/delete` | `services/tool-api/app/api/files.py:686` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| POST `/webapp/folders/{folder_id}/rename` | `services/tool-api/app/api/files.py:674` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| GET `/webapp/grid` | `services/tool-api/app/api/schedule.py:49` | schedule.read.all | сессия Mini App | server.py, test_cm10_reports.py, test_miniapp_system.py (+3) |
| GET `/webapp/month-grid` | `services/tool-api/app/api/schedule.py:111` | schedule.read.all | сессия Mini App | server.py, test_webapp_month_grid.py, test_webapp_week_page.py |
| GET `/webapp/notes/list` | `services/tool-api/app/api/notes.py:263` | notes.write | сессия Mini App | server.py, test_notes_api.py |
| POST `/webapp/notes/save` | `services/tool-api/app/api/notes.py:272` | notes.write | сессия Mini App | server.py, test_cm08_miniapp.py, test_notes_api.py |
| GET `/webapp/person/info` | `services/tool-api/app/api/people.py:684` | participants.view | сессия Mini App | server.py, test_org_miniapp_screens.py |
| POST `/webapp/person/tariff` | `services/tool-api/app/api/people.py:710` | budget.write | сессия Mini App | server.py, test_org_miniapp_screens.py |
| POST `/webapp/slots/find` | `services/tool-api/app/api/schedule.py:1059` | schedule.slots | сессия Mini App | server.py, test_org_miniapp_screens.py |
| GET `/webapp/slots/people` | `services/tool-api/app/api/schedule.py:1027` | schedule.slots | сессия Mini App | server.py, test_master_role.py, test_org_miniapp_screens.py |
| GET `/webapp/tags/pending` | `services/tool-api/app/api/files.py:738` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| POST `/webapp/tags/{tag_id}/approve` | `services/tool-api/app/api/files.py:747` | files.access | сессия Mini App | server.py, test_files_actions_screen.py |
| POST `/webapp/tx/save` | `services/tool-api/app/api/budget.py:452` | budget.write | сессия Mini App | server.py, test_cm08_miniapp.py, test_org_miniapp_screens.py |

### 7b. Служебный Bearer-токен

Маршрут зовёт другой сервис, а не человек: в теле сверяется `Authorization: Bearer {TOOL_API_TOKEN}` (`hmac.compare_digest`). У `POST /admin/bootstrap` дополнительно сверяется `ADMIN_CHAT_ID`.

| Действие | Точка входа | Тест |
|---|---|---|
| POST `/admin/bootstrap` | `services/tool-api/app/api/admin.py:95` | нет |
| POST `/admin/files/cold-rotate` | `services/tool-api/app/api/admin.py:1442` | test_cold.py, test_service_routes_auth.py |
| GET `/admin/files/storage` | `services/tool-api/app/api/admin.py:1416` | test_cold.py, test_service_routes_auth.py |
| GET `/tools/participants/search` | `services/tool-api/app/api/people.py:606` | test_gateway_registration.py, test_registration.py |

### 7c. Ручная проверка `"*" in caps`

Возможность проверяется не `core.require(...)`, а руками в теле обработчика: `"*" in caps or "<право>" in caps`. Одно правило — два пути проверки.

| Действие | Точка входа | Право | В теле проверяет |
|---|---|---|---|
| POST `/tools/availability/reconcile` | `services/tool-api/app/api/schedule.py:1487` | schedule.availability.self | schedule.read.all |
| GET `/tools/availability/week` | `services/tool-api/app/api/schedule.py:733` | schedule.availability.self | schedule.read.all |

## 8. Эндпоинты, которые не зовёт ни один интерфейс

Кандидаты на удаление: ни экран, ни бот, ни скрипт не обращаются по этому пути. Тест упомянут отдельно — если он есть, путь живёт ради теста.

| Действие | Точка входа | Право | Тест |
|---|---|---|---|
| POST `/admin/files/cold-rotate` | `services/tool-api/app/api/admin.py:1442` | сервисный токен | test_cold.py, test_service_routes_auth.py |
| GET `/admin/files/storage` | `services/tool-api/app/api/admin.py:1416` | сервисный токен | test_cold.py, test_service_routes_auth.py |
| GET `/files/{file_id}/access` | `services/tool-api/app/api/files.py:257` | любой активный | test_file_access.py, test_files_studio_roles.py |
| POST `/files/{file_id}/folder` | `services/tool-api/app/api/files.py:441` | любой активный | test_files_actions_screen.py, test_folder_order.py |
| GET `/folders/{folder_id}/files` | `services/tool-api/app/api/files.py:413` | любой активный | test_files_actions_screen.py, test_files_delete.py, test_folder_order.py |
| POST `/tools/agent/files` | `services/tool-api/app/api/admin.py:1474` | любой активный | нет |
| POST `/tools/availability/reconcile` | `services/tool-api/app/api/schedule.py:1487` | schedule.availability.self | test_availability_sheet.py |
| POST `/tools/availability/sheet` | `services/tool-api/app/api/schedule.py:1256` | schedule.availability.self | test_availability_sheet.py |
| GET `/tools/availability/sheet/mismatches` | `services/tool-api/app/api/schedule.py:1444` | schedule.availability.self | test_availability_sheet.py |
| GET `/tools/calendar/health` | `services/tool-api/app/api/events.py:825` | любой активный | test_calendar_api.py |
| POST `/tools/dues/accrue` | `services/tool-api/app/api/finance.py:65` | budget.write | test_cm04_money.py, test_dues_accrue.py |
| GET `/tools/feedback/mine` | `services/tool-api/app/api/admin.py:167` | любой активный | test_cm06_feedback.py |
| GET `/tools/feedback/open` | `services/tool-api/app/api/feedback.py:68` | feedback.triage | test_feedback_triage.py |
| GET `/tools/finance/granularity` | `services/tool-api/app/api/finance.py:190` | budget.read | нет |
| POST `/tools/finance/granularity` | `services/tool-api/app/api/finance.py:200` | budget.import | нет |
| GET `/tools/finance/sources` | `services/tool-api/app/api/finance.py:224` | budget.read | нет |
| POST `/tools/finance/sources` | `services/tool-api/app/api/finance.py:241` | budget.import | нет |
| DELETE `/tools/finance/sources/{source_id}` | `services/tool-api/app/api/finance.py:260` | budget.import | нет |
| GET `/tools/intent/categories` | `services/tool-api/app/api/admin.py:622` | любой активный | test_intent_log.py |
| GET `/tools/intent/report` | `services/tool-api/app/api/admin.py:670` | audit.view | test_intent_log.py, test_smoke_api_fixes.py |
| POST `/tools/intent/resolve` | `services/tool-api/app/api/admin.py:632` | любой активный | test_intent_log.py |
| POST `/tools/notes/{note_id}` | `services/tool-api/app/api/notes.py:253` | notes.write | test_notes_api.py |
| GET `/tools/participants/find` | `services/tool-api/app/api/people.py:624` | participants.view | test_find_person.py |
| GET `/tools/venues` | `services/tool-api/app/api/events.py:818` | любой активный | test_venues_api.py |
| GET `/webapp/admin/invites` | `services/tool-api/app/api/admin.py:894` | сессия Mini App + registration.code.issue | test_admin_invites.py |
| POST `/webapp/admin/sheet-retry` | `services/tool-api/app/api/admin.py:868` | сессия Mini App + budget.write | test_webapp_sheet_retry.py |
| POST `/webapp/availability/sheet` | `services/tool-api/app/api/schedule.py:1264` | сессия Mini App | нет |
| GET `/webapp/availability/template` | `services/tool-api/app/api/schedule.py:1098` | сессия Mini App | test_gateway_quiet_parity.py |
| GET `/webapp/my` | `services/tool-api/app/api/people.py:381` | сессия Mini App | server.py, test_admin_people_actions.py, test_smoke_api_fixes.py (+1) |

## 9. Самопроверка

* маршрутов в контракте: 234, в коде: 234
* нет в коде, но есть в контракте: 0
* есть в коде, но нет в контракте: 0
<!-- audit-actions:end -->
