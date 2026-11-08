"""Экраны Mini App, которые открываются из разделов (дизайн-система версии 2).

Каждый экран — свой модуль: взносы / уведомления / обратная связь / рассылка /
сверка / окна / анкета. Этот файл оставлен точкой импорта: маршруты
(`pages/routes.py`), стенд и тесты берут экраны отсюда, поэтому старые имена
продолжают работать. Раньше здесь лежали все семь экранов на 1326 строк.

Правила, общие для всех:
* только классы `.ui-*` из `static/webapp-ui.css`, без собственных стилей и таблиц;
* одна главная кнопка `.ui-btn--primary` на экран;
* `alert`/`confirm` не используются: подтверждение — `SVOI.sheet`, результат —
  `SVOI.toast` (docs/85 §1, п. 11);
* поля только 16 px, дата — `.ui-datefield`, интервал — `.ui-range`;
* текст экранируется на сервере (`layout.esc`), данные приходят с `/webapp/*`,
  токен в запрос подставляет оболочка (`webapp-shell.js`).
"""

from __future__ import annotations

from app.pages.misc_broadcast import BROADCAST_PAGE
from app.pages.misc_dues import DUES_PAGE
from app.pages.misc_feedback import FEEDBACK_PAGE
from app.pages.misc_notifications import NOTIFICATIONS_PAGE
from app.pages.misc_profile import PROFILE_PAGE
from app.pages.misc_reconcile import RECONCILE_PAGE
from app.pages.misc_slots import SLOTS_PAGE

__all__ = [
    "DUES_PAGE",
    "NOTIFICATIONS_PAGE",
    "FEEDBACK_PAGE",
    "BROADCAST_PAGE",
    "RECONCILE_PAGE",
    "SLOTS_PAGE",
    "PROFILE_PAGE",
]
