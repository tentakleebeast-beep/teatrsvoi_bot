"""Страницы Mini App: единая точка импорта для совместимости.

До переработки в этом файле лежали все шестнадцать страниц — каждая со своим
`<style>` внутри Python-строки. Из-за этого одинаковые вещи выглядели по-разному,
а дизайн-система существовала отдельно от экранов (`docs/development/84`, П-1).

Теперь каждая область — свой модуль в `app/pages/`:

| Модуль | Экраны |
|---|---|
| `pages/layout.py` | каркас: шапка, вкладки, форматирование |
| `pages/home.py` | «Главная», «Ещё/Управление» |
| `pages/week.py` | «Занятость», сводка занятости |
| `pages/money.py` | «Деньги», форма операции |
| `pages/admin.py` | точка импорта админских экранов (см. `admin_*`) |
| `pages/admin_core.py` | общее ядро админских экранов: стили, запросы, листы |
| `pages/admin_people.py` | люди |
| `pages/admin_pending.py` | заявки |
| `pages/admin_rights.py` | права |
| `pages/admin_archive.py` | архив |
| `pages/admin_audit.py` | аудит |
| `pages/admin_invites.py` | приглашения |
| `pages/admin_feedback.py` | обращения |
| `pages/admin_notifications.py` | общие настройки уведомлений студии (FR-G17) |
| `pages/misc.py` | точка импорта экранов разделов (см. `misc_*`) |
| `pages/misc_common.py` | общий JS: даты, суммы, числа |
| `pages/misc_dues.py` | взносы |
| `pages/misc_notifications.py` | уведомления |
| `pages/misc_feedback.py` | обратная связь |
| `pages/misc_broadcast.py` | рассылка |
| `pages/misc_reconcile.py` | сверка листов |
| `pages/misc_slots.py` | поиск окон |
| `pages/misc_profile.py` | анкета участника |
| `pages/person.py` | карточка участника (старый экран, `/webapp/person`) |
| `pages/kit.py` | справочник компонентов `/webapp/ui` |

Этот файл оставлен ради совместимости: старые имена (`PAGE`, `DAYS_PAGE`,
`ADMIN_PAGE`, …) нужны маршрутам, стенду `tests/webapp/server.py` и тестам, которые
проверяют JS страниц. Соответствие старых имён новым экранам — `docs/development/86` §3.
"""

from __future__ import annotations

from app.pages import admin as _admin
from app.pages import files as _files
from app.pages import home as _home
from app.pages import kit as _kit
from app.pages import misc as _misc
from app.pages import money as _money
from app.pages import notes as _notes
from app.pages import person as _person
from app.pages import week as _week

# --- актуальные экраны ------------------------------------------------------

PAGE = _misc.PROFILE_PAGE                       # анкета участника
HOME_PAGE = _home.HOME_PAGE                     # «Главная» (вкладка)
MORE_PAGE = _home.MORE_PAGE                     # «Ещё» / «Управление»
WEEK_PAGE = _week.WEEK_PAGE                     # «Занятость» (вкладка)
SUMMARY_PAGE = _week.SUMMARY_PAGE               # сводка занятости
MONTH_PAGE = _week.MONTH_PAGE                   # сетка месяца «люди × дни» (З9)
MONEY_PAGE = _money.MONEY_PAGE                  # «Деньги» (вкладка)
TX_PAGE = _money.TX_PAGE                        # форма операции
DUES_PAGE = _misc.DUES_PAGE                     # мои взносы
NOTIFICATIONS_PAGE = _misc.NOTIFICATIONS_PAGE   # уведомления
FEEDBACK_PAGE = _misc.FEEDBACK_PAGE             # обратная связь автора
BROADCAST_PAGE = _misc.BROADCAST_PAGE           # рассылка
RECONCILE_PAGE = _misc.RECONCILE_PAGE           # сверка листов
SLOTS_PAGE = _misc.SLOTS_PAGE                   # поиск окон
UI_KIT_PAGE = _kit.UI_KIT_PAGE                  # справочник компонентов
NOTES_PAGE = _notes.NOTES_PAGE                  # заметки: личные и общие (FR-F7)

PEOPLE_PAGE = _admin.PEOPLE_PAGE
PENDING_PAGE = _admin.PENDING_PAGE
RIGHTS_PAGE = _admin.RIGHTS_PAGE
ARCHIVE_PAGE = _admin.ARCHIVE_PAGE
AUDIT_PAGE = _admin.AUDIT_PAGE
INVITES_PAGE = _admin.INVITES_PAGE
FEEDBACK_QUEUE_PAGE = _admin.FEEDBACK_QUEUE_PAGE
AGENT_PAGE = _admin.AGENT_PAGE                  # разбор агентных сессий (A-5)
DUES_ADMIN_PAGE = _admin.DUES_ADMIN_PAGE        # взносы: тариф и начисление (Д6, Д8)
NOTIFICATION_DEFAULTS_PAGE = _admin.NOTIFICATION_DEFAULTS_PAGE   # общие уведомления (FR-G17)

# --- старые имена, которые ещё читают маршруты, стенд и тесты ---------------
# Каждое имя — ровно тот экран, который заменяет прежний (docs/development/86 §3).

DAYS_PAGE = _week.WEEK_PAGE          # был дневной список недели
MY_PAGE = _week.WEEK_PAGE            # был лист «16 часов × 30 дней» — заменён листом дня
GRID_PAGE = _week.SUMMARY_PAGE       # была сводная сетка месяца
SEARCH_PAGE = _admin.PEOPLE_PAGE     # был отдельный поиск людей
ADMIN_PAGE = _admin.PEOPLE_PAGE      # была одна страница с лентой вкладок

# Карточка участника ещё не переехала в лист человека (см. pages/person.py).
PERSON_PAGE = _person.PERSON_PAGE

# Файлы и папки пришли из main отдельной веткой работ: разметка пока старая,
# контракт — `tests/integration/test_webapp_files.py`. Переверстка — после плана 88.
FILES_PAGE = _files.FILES_PAGE
FOLDER_PAGE = _files.FOLDER_PAGE
