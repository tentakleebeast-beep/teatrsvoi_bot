"""Админские экраны Mini App: разделы «Управления» (docs/development/86 §5).

Каждый раздел — свой модуль (люди / заявки / права / архив / аудит / приглашения /
обращения), общее ядро — в `admin_core.py`. Этот файл оставлен точкой импорта:
маршруты (`pages/routes.py`), стенд и тесты берут экраны отсюда, поэтому старые
имена продолжают работать. Раньше здесь лежали все семь экранов на 1251 строку.

Контракты `docs/development/73` не менялись: экраны читают
`GET /webapp/admin/overview` и зовут те же действия `/webapp/admin/*`, права
проверяются на сервере. Страница лишь не рисует то, что всё равно получит 403.
"""

from __future__ import annotations

from app.pages.admin_agent import AGENT_PAGE
from app.pages.admin_archive import ARCHIVE_PAGE
from app.pages.admin_audit import AUDIT_PAGE
from app.pages.admin_dues import DUES_ADMIN_PAGE
from app.pages.admin_feedback import FEEDBACK_QUEUE_PAGE
from app.pages.admin_invites import INVITES_PAGE
from app.pages.admin_notifications import NOTIFICATION_DEFAULTS_PAGE
from app.pages.admin_pending import PENDING_PAGE
from app.pages.admin_people import PEOPLE_PAGE
from app.pages.admin_rights import RIGHTS_PAGE

__all__ = [
    "PEOPLE_PAGE",
    "PENDING_PAGE",
    "RIGHTS_PAGE",
    "ARCHIVE_PAGE",
    "AUDIT_PAGE",
    "INVITES_PAGE",
    "FEEDBACK_QUEUE_PAGE",
    "AGENT_PAGE",
    "DUES_ADMIN_PAGE",
    "NOTIFICATION_DEFAULTS_PAGE",
]
