"""Архив: без `participants.manage` списка нет, а отказ — общим словарём (21–23).

Обоснование: FR-A7 (архив и возврат — по праву, а не по просмотру),
docs/development/91 принципы 8, 9, Б3, docs/development/73 (права экранов панели).
Отчёт 142 §3 строка 26, карта t_c6a13266.

У `org` и `master` есть `participants.view` и нет `participants.manage`
(`db/init/008`), поэтому экран рисовал 12 ушедших людей и кнопки «Вернуть в
активные», которых сервер не даст: `/webapp/admin/archive` и
`/webapp/admin/unarchive` требуют `participants.manage`. Хуже вида — блок архива
строился в `/webapp/admin/overview` под `"people" in allowed`, то есть данные,
закрытые правом, уходили в браузер. Теперь блок `archive` в `allowed` и в
`blocks` приходит только с `participants.manage`, а экран без права не рисует
ни списка, ни поиска и печатает ту же плашку, что видит `troupe` при 403
(`ERRORS.admin_panel_forbidden`) — один словарь состояний с экранами 21–23.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_archive_rights.py \\
        -q --browser chromium
"""

from __future__ import annotations

DENIED = "Раздел доступен организатору и администратору."
RESTORE = "Вернуть в активные"

ORG = {"X-SVOI-Role": "org"}
MASTER = {"X-SVOI-Role": "master"}
TROUPE = {"X-SVOI-Role": "troupe"}


def _settled(page) -> None:
    """Ждём, пока страница получила ответ панели (запросы после входа стихли)."""
    page.wait_for_load_state("networkidle")


def _screen_text(page) -> str:
    return page.locator(".ui-screen").inner_text()


def _no_right(page) -> None:
    """Без права: ни людей, ни кнопок, ни поиска — только плашка общего словаря."""
    text = _screen_text(page)
    assert "Ушедший" not in text, f"архивные люди уехали в браузер без права: {text}"
    assert RESTORE not in text, f"кнопка без права: {text}"
    assert "Архив пуст" not in text, "пустота читается как «возвращать нечего»"
    assert not page.locator("#archive-search-field").is_visible(), \
        "поиск над пустотой зовёт искать то, чего нет"
    assert page.locator("#archive-list").inner_text().strip() == "", \
        "под отказом остался список архива"
    assert page.locator("#status").inner_text().strip() == DENIED
    assert page.svoi_errors == [], page.svoi_errors


def test_org_без_права_не_видит_архива(app_page) -> None:
    """`participants.manage` у org нет: данные архива не должны приходить вовсе."""
    page = app_page("/webapp/admin/t/archive", headers=ORG)
    _settled(page)
    _no_right(page)


def test_master_без_права_не_видит_архива(app_page) -> None:
    """У master тоже только `participants.view`: ни людей, ни кнопки возврата."""
    page = app_page("/webapp/admin/t/archive", headers=MASTER)
    _settled(page)
    _no_right(page)


def test_troupe_видит_то_же_сообщение(app_page) -> None:
    """403 панели и «блока нет» — одно состояние: две роли без права видят одно."""
    page = app_page("/webapp/admin/t/archive", headers=TROUPE)
    _settled(page)
    assert page.locator("#status").inner_text().strip() == DENIED
    assert page.locator("#archive-list").inner_text().strip() == ""
    assert page.svoi_errors == [], page.svoi_errors


def test_admin_видит_архив_и_кнопку_возврата(app_page) -> None:
    """Администратору право дано: строки и кнопка на месте, статус пуст."""
    page = app_page("/webapp/admin/t/archive")
    _settled(page)

    text = _screen_text(page)
    assert "Ушедший" in text, "у администратора пропал архив"
    assert RESTORE in text
    assert page.locator("#archive-search-field").is_visible()
    assert "1 человек в архиве" in page.locator("#archive-count").inner_text()
    assert DENIED not in text
    assert page.locator("#status").inner_text().strip() == ""
    assert page.svoi_errors == [], page.svoi_errors


def test_роль_не_решает_право_решает_ответ_сервера(app_page) -> None:
    """Сервер сказал «управлять людьми нельзя» — список не рисуется даже «админу».

    Право приходит данными (`actions.manage_rights`), а не выводится из роли:
    иначе экран снова начнёт обещать то, что сервер отклонит
    (`docs/development/86` §7).
    """
    page = app_page("/webapp/admin/t/archive")

    def without_right(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=(
            '{"ok": true, "allowed": ["people", "pending", "rights"],'
            ' "actions": {"manage_rights": false},'
            ' "blocks": {"people": {"counts": {"active": 1, "pending": 0}, "data": []},'
            ' "archive": {"data": [{"id": 9, "display_name": "Ушедший",'
            ' "roles": ["troupe"], "status": "archived"}]}}}'))

    page.route("**/webapp/admin/overview", without_right)
    page.reload(wait_until="load")
    _settled(page)

    text = _screen_text(page)
    assert "Ушедший" not in text, f"экран взял архив из блока без права: {text}"
    assert page.locator("#status").inner_text().strip() == DENIED
    assert page.svoi_errors == [], page.svoi_errors
