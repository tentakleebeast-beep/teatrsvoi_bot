"""Обращения: без `feedback.triage` очередь не запрашивается, а экран говорит, кто разбирает.

Отчёт 142 §3 строка 27, принципы 9 и Б3 из docs/development/91, docs/reports/142 §5.1,
карта t_83a64c1f. У org, master и troupe нет `feedback.triage` (V-005: обращения идут
администратору дайджестом), но экран писал «Открытых обращений нет. Новое обращение
появится здесь, когда кто-то напишет из бота» — человек ждал обращений, которых
никогда не увидит. Причина: любой отказ API (`403 capability required: feedback.triage`,
а у troupe ещё и `403 admin_panel_forbidden`) превращался в пустой список.

Теперь право проверяется до очереди: экран смотрит `allowed` ответа
`/webapp/admin/overview` и без раздела `feedback` очередь не запрашивает вовсе,
показывая «Обращения разбирает администратор». У администратора очередь на месте,
пустое состояние прежнее.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_feedback_rights.py \\
        -q --browser chromium
"""

from __future__ import annotations

import pytest

from tests.webapp.server import FEEDBACK_EVENTS

DENIED = "Обращения разбирает администратор"
EMPTY = "Открытых обращений нет"

ORG = {"X-SVOI-Role": "org"}
MASTER = {"X-SVOI-Role": "master"}
TROUPE = {"X-SVOI-Role": "troupe"}


@pytest.fixture(autouse=True)
def _clean_events() -> None:
    """Журнал запросов стенда — на каждый тест свой: проверяем, был ли запрос вовсе."""
    FEEDBACK_EVENTS.clear()


def _settled(page) -> None:
    """Ждём, пока страница получила ответ панели (запросы после входа стихли)."""
    page.wait_for_load_state("networkidle")


def _screen_text(page) -> str:
    return page.locator(".ui-screen").inner_text()


def test_org_без_права_не_запрашивает_очередь_и_говорит_кто_разбирает(app_page) -> None:
    """У org панель открыта (`participants.view`), а `feedback` в `allowed` нет."""
    page = app_page("/webapp/admin/t/feedback", headers=ORG)
    _settled(page)

    text = _screen_text(page)
    assert DENIED in text, text
    assert EMPTY not in text, f"у org снова пустота вместо права: {text}"
    assert FEEDBACK_EVENTS == [], f"без права очередь всё-таки запрошена: {FEEDBACK_EVENTS}"
    assert page.svoi_errors == [], page.svoi_errors


def test_master_без_права_не_запрашивает_очередь_и_говорит_кто_разбирает(app_page) -> None:
    """У master есть только `participants.view`: та же картина, что у org."""
    page = app_page("/webapp/admin/t/feedback", headers=MASTER)
    _settled(page)

    text = _screen_text(page)
    assert DENIED in text, text
    assert EMPTY not in text, f"у master снова пустота вместо права: {text}"
    assert FEEDBACK_EVENTS == [], f"без права очередь всё-таки запрошена: {FEEDBACK_EVENTS}"
    assert page.svoi_errors == [], page.svoi_errors


def test_troupe_закрытая_панель_это_тоже_нет_права(app_page) -> None:
    """У troupe панель закрыта целиком (403): право не подтверждено, очередь не нужна."""
    page = app_page("/webapp/admin/t/feedback", headers=TROUPE)
    _settled(page)

    text = _screen_text(page)
    assert DENIED in text, text
    assert EMPTY not in text, f"у troupe снова пустота вместо права: {text}"
    assert FEEDBACK_EVENTS == [], f"без права очередь всё-таки запрошена: {FEEDBACK_EVENTS}"
    assert page.svoi_errors == [], page.svoi_errors


def test_admin_видит_очередь_и_прежнее_пустое_состояние(app_page) -> None:
    """У администратора право есть: очередь запрошена, пустое состояние прежнее."""
    page = app_page("/webapp/admin/t/feedback")
    _settled(page)

    text = _screen_text(page)
    assert EMPTY in text, text
    assert DENIED not in text, f"у администратора отказ вместо очереди: {text}"
    assert FEEDBACK_EVENTS == ["admin"], f"очередь администратора не запрошена: {FEEDBACK_EVENTS}"
    assert page.locator("#status").inner_text().strip() == ""
    assert page.svoi_errors == [], page.svoi_errors
