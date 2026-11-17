# 167. Единый граф переходов: чат бота → экраны Mini App

**Дата:** 17.11.2026. **Собирается:** `scripts/build_screen_map.py` — не правится руками.
**Заменяет:** `docs/archive/prototypes/44-menu-map.html` (описывал меню и шесть экранов;
в коде четыре вкладки и 36 экранов).
**Связано:** `docs/development/92-navigation-model.md` (модель навигации),
`docs/development/141-miniapp-design-review.md` (инвентарь экранов),
`docs/development/91-design-system.md` (вид), `docs/development/173-prototype-workspace.md`
(как с этим работаем).

Источник истины — код: `tests/webapp/server.py` отдаёт те же страницы, что боевой tool-api,
поэтому рёбра графа — настоящие `<a href>` со стенда, а подписи — текст кнопок. Документ
устаревает только вместе с кодом: пересборка — одна команда.

## 1. Граф

Полоса вкладок («Главная · Время · Деньги · Управление») есть на каждом экране и ведёт в
четыре корня. На графе она не нарисована: 35 × 4 одинаковых линий сделали бы схему нечитаемой.
Стрелка «‹» — возврат на уровень выше.

```mermaid
flowchart LR
  chat["Чат бота: /start, кнопка «Меню»"]
  subgraph g1["Вкладки Mini App"]
    direction TB
    nwebapp["Профиль"]
    nwebapp_home["Главная"]
    nwebapp_week["Занятость"]
    nwebapp_money["Деньги"]
    nwebapp_more["Ещё"]
  end
  subgraph g2["Время"]
    direction TB
    nwebapp_week_summary["Сводка занятости"]
    nwebapp_week_month["Сетка месяца"]
    nwebapp_slots["Поиск окон"]
  end
  subgraph g3["Деньги"]
    direction TB
    nwebapp_tx["Операция"]
    nwebapp_dues["Мои взносы"]
  end
  subgraph g4["Опросы"]
    direction TB
    nwebapp_polls["Опросы"]
    nwebapp_polls_edit["Конструктор опроса"]
    nwebapp_polls_results["Результаты опроса"]
    nwebapp_polls_mine["Мои опросы"]
    nwebapp_polls_answer["Опрос"]
  end
  subgraph g5["Файлы"]
    direction TB
    nwebapp_files["Файлы"]
    nwebapp_folder["Папка"]
    nwebapp_file["Файл"]
  end
  subgraph g6["Прочее"]
    direction TB
    nwebapp_person["Карточка участника"]
    nwebapp_notifications["Уведомления (свой)"]
    nwebapp_feedback["Обратная связь"]
    nwebapp_services["Сервисы"]
    nwebapp_broadcast["Рассылка"]
    nwebapp_reconcile["Сверка листов"]
  end
  subgraph g7["Управление (разделы)"]
    direction TB
    nwebapp_admin_t_people["Люди"]
    nwebapp_admin_t_dues["Взносы"]
    nwebapp_admin_t_pending["Заявки"]
    nwebapp_admin_t_rights["Права"]
    nwebapp_admin_t_archive["Архив"]
    nwebapp_admin_t_audit["Аудит"]
    nwebapp_admin_t_agent["Разбор агента"]
    nwebapp_admin_t_notifications["Уведомления (раздел)"]
    nwebapp_admin_t_invites["Приглашения"]
    nwebapp_admin_t_feedback["Обращения"]
    nwebapp_notes["Заметки"]
  end
  nwebapp -->|"‹"| nwebapp_more
  nwebapp_tx -->|"‹"| nwebapp_money
  nwebapp_week -->|"Сводка занятости"| nwebapp_week_summary
  nwebapp_week_summary -->|"Найти окно для события"| nwebapp_slots
  nwebapp_week_summary -->|"Сетка месяца"| nwebapp_week_month
  nwebapp_week_month -->|"‹"| nwebapp_week_summary
  nwebapp_money -->|"Записать операцию"| nwebapp_tx
  nwebapp_person -->|"‹"| nwebapp_home
  nwebapp_feedback -->|"‹"| nwebapp_more
  nwebapp_slots -->|"‹"| nwebapp_week
  nwebapp_admin_t_people -->|"‹"| nwebapp_more
  nwebapp_admin_t_dues -->|"‹"| nwebapp_more
  nwebapp_admin_t_pending -->|"‹"| nwebapp_more
  nwebapp_admin_t_rights -->|"‹"| nwebapp_more
  nwebapp_admin_t_archive -->|"‹"| nwebapp_more
  nwebapp_admin_t_audit -->|"‹"| nwebapp_more
  nwebapp_admin_t_agent -->|"‹"| nwebapp_more
  nwebapp_admin_t_notifications -->|"‹"| nwebapp_more
  nwebapp_admin_t_notifications -->|"Мои настройки уведомлений"| nwebapp_notifications
  nwebapp_admin_t_invites -->|"‹"| nwebapp_more
  nwebapp_admin_t_feedback -->|"‹"| nwebapp_more
  nwebapp_home -->|"Заявки"| nwebapp_admin_t_pending
  nwebapp_home -->|"Обращения"| nwebapp_more
  nwebapp_home -->|"Крупная сумма"| nwebapp_money
  nwebapp_home -->|"Отметить занятость"| nwebapp_week
  nwebapp_home -->|"Записать операцию"| nwebapp_tx
  nwebapp_notifications -->|"‹"| nwebapp_more
  nwebapp_dues -->|"‹"| nwebapp_money
  nwebapp_broadcast -->|"‹"| nwebapp_more
  nwebapp_reconcile -->|"‹"| nwebapp_more
  nwebapp_services -->|"‹"| nwebapp_more
  nwebapp_files -->|"‹"| nwebapp_more
  nwebapp_files -->|"Репетиция 12 октября.m4a18,0…"| nwebapp_file
  nwebapp_files -->|"Архив 2024 и ещё 5"| nwebapp_folder
  nwebapp_folder -->|"Открыть «Файлы» и ещё 1"| nwebapp_files
  nwebapp_file -->|"Открыть Файлы и ещё 1"| nwebapp_files
  nwebapp_notes -->|"‹"| nwebapp_more
  nwebapp_polls -->|"‹"| nwebapp_more
  nwebapp_polls -->|"Анкета организаторам"| nwebapp_polls_results
  nwebapp_polls -->|"Что улучшить в расписании и…"| nwebapp_polls_edit
  nwebapp_polls_edit -->|"‹"| nwebapp_polls
  nwebapp_polls_results -->|"‹"| nwebapp_polls
  nwebapp_polls_mine -->|"‹"| nwebapp_home
  nwebapp_polls_mine -->|"Что по опросам? и ещё 1"| nwebapp_polls_answer
  nwebapp_polls_answer -->|"К списку и ещё 1"| nwebapp_polls_mine
  nwebapp_more -->|"Заявки"| nwebapp_admin_t_pending
  nwebapp_more -->|"Люди"| nwebapp_admin_t_people
  nwebapp_more -->|"Обращения"| nwebapp_admin_t_feedback
  nwebapp_more -->|"Рассылка"| nwebapp_broadcast
  nwebapp_more -->|"Сверка листов"| nwebapp_reconcile
  nwebapp_more -->|"Профиль"| nwebapp
  nwebapp_more -->|"Файлы студии"| nwebapp_files
  nwebapp_more -->|"Заметки"| nwebapp_notes
  nwebapp_more -->|"Уведомления"| nwebapp_notifications
  nwebapp_more -->|"Обратная связь"| nwebapp_feedback
  chat -->|"Открыть неделю"| nwebapp_week
  chat -->|"Внести операцию"| nwebapp_tx
  chat -->|"Открыть поиск окон"| nwebapp_slots
  chat -->|"Открыть анкету"| nwebapp
  chat -->|"Открыть заметки"| nwebapp_notes
```

## 2. Экраны

Права — из реестра `SECTIONS` (`services/tool-api/app/webapp.py`): экран рисуется, только если
возможность есть; данные всё равно проверяются в API.

| Адрес | Экран | Права |
|---|---|---|
| `/webapp` | Профиль | — |
| `/webapp/home` | Главная | — |
| `/webapp/money` | Деньги | — |
| `/webapp/more` | Ещё | — |
| `/webapp/week` | Занятость | — |
| `/webapp/admin/t/agent` | Разбор агента | admin.panel |
| `/webapp/admin/t/archive` | Архив | participants.manage |
| `/webapp/admin/t/audit` | Аудит | audit.view |
| `/webapp/admin/t/dues` | Взносы | budget.write |
| `/webapp/admin/t/feedback` | Обращения | feedback.triage |
| `/webapp/admin/t/invites` | Приглашения | registration.code.issue |
| `/webapp/admin/t/notifications` | Уведомления | admin.panel |
| `/webapp/admin/t/pending` | Заявки | registration.approve |
| `/webapp/admin/t/people` | Люди | participants.view |
| `/webapp/admin/t/rights` | Права | participants.manage |
| `/webapp/broadcast` | Рассылка | notify.send |
| `/webapp/dues` | Мои взносы | — |
| `/webapp/feedback` | Обратная связь | — |
| `/webapp/file` | Файл | — |
| `/webapp/files` | Файлы | files.access |
| `/webapp/folder` | Папка | — |
| `/webapp/notes` | Заметки | notes.write |
| `/webapp/notifications` | Уведомления | — |
| `/webapp/person` | Карточка участника | — |
| `/webapp/polls` | Опросы | poll.manage |
| `/webapp/polls/answer` | Опрос | — |
| `/webapp/polls/edit` | Конструктор опроса | — |
| `/webapp/polls/mine` | Мои опросы | — |
| `/webapp/polls/results` | Результаты опроса | — |
| `/webapp/reconcile` | Сверка листов | budget.import |
| `/webapp/services` | Сервисы | admin.panel |
| `/webapp/slots` | Поиск окон | — |
| `/webapp/tx` | Операция | — |
| `/webapp/ui` | Элементы интерфейса | — |
| `/webapp/week/month` | Сетка месяца | — |
| `/webapp/week/summary` | Сводка занятости | — |

## 3. Старые адреса (редиректы 301)

Адреса не удаляются: ссылки в чате и закладках живут дальше.

| Было | Стало |
|---|---|
| `/webapp/admin` | `/webapp/admin/t/people` |
| `/webapp/assign` | `/webapp/slots` |
| `/webapp/days` | `/webapp/week` |
| `/webapp/my/sheet` | `/webapp/week` |
| `/webapp/people` | `/webapp/admin/t/people` |
| `/webapp/schedule` | `/webapp/week/summary` |
| `/webapp/search` | `/webapp/admin/t/people` |

## 4. Как обновить

```
# стенд
env -u PYTHONPATH .venv/bin/python -m uvicorn tests.webapp.server:app --port 8799
# граф и атлас экранов
env -u PYTHONPATH .venv/bin/python scripts/build_screen_map.py
```

Скрипт падает, если на экране есть ссылка на адрес, которого нет среди экранов и редиректов:
это дефект стенда или приложения, а не повод нарисовать стрелку «куда-нибудь».

## 5. Что не попало на граф

- **Пункты меню чата** (Деньги · Время · Админ · Профиль · Помощь) — это `callback_data`,
  а не адреса: они не ведут в Mini App, а работают в чате. Их состав — `keyboards.menu_groups`.
- **Динамические кнопки** бота: адрес собирается из переменной (`screen`, константы страниц).
  Они видны на графе как сами экраны, к которым ведут.
- **Снимки экранов** — в `docs/reports/171-screens-atlas.html` (собирается тем же прогоном).


