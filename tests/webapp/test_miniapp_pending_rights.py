"""Заявки: без права экран говорит о праве, а не пустотой (принцип 9, Б3, docs/development/91).

Отчёт 142 §3 строка 21, карта t_8111b38d. У `master` есть только
`participants.view`: `/webapp/admin/overview` отдаёт блоки списком `allowed`, и
`pending` в нём нет (`registration.approve` / `participants.manage`). Экран читал
отсутствующий блок как пустой список и рисовал «Заявок нет» — молчаливую пустоту.
У `troupe` в том же месте 403 и честное «Раздел доступен организатору и
администратору», то есть две роли без права видели разное. Теперь обе видят одно
и то же сообщение из общего словаря ядра (`ERRORS.admin_panel_forbidden`).

А9 того же отчёта: «Принять» стояло главной кнопкой в каждой строке — четыре
заявки, четыре главных. По принципу 1 (одна главная на экран) кнопки строк
стали обычными.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_pending_rights.py \\
        -q --browser chromium
"""

from __future__ import annotations

DENIED = "Раздел доступен организатору и администратору."

MASTER = {"X-SVOI-Role": "master"}
TROUPE = {"X-SVOI-Role": "troupe"}


def _settled(page) -> None:
    """Ждём, пока страница получила ответ панели (запросы после входа стихли)."""
    page.wait_for_load_state("networkidle")


def _screen_text(page) -> str:
    return page.locator(".ui-screen").inner_text()


def test_master_читает_отказ_а_не_пустоту(app_page) -> None:
    """Блок `pending` не пришёл — экран объясняет право, а не «Заявок нет»."""
    page = app_page("/webapp/admin/t/pending", headers=MASTER)
    _settled(page)

    text = _screen_text(page)
    assert "Заявок нет" not in text, f"у master снова молчаливая пустота: {text}"
    assert DENIED in text, text
    assert page.locator("#status").inner_text().strip() == DENIED
    assert page.locator("#pending-list").inner_text().strip() == "", \
        "под отказом остался список заявок"
    assert page.svoi_errors == [], page.svoi_errors


def test_troupe_видит_то_же_сообщение(app_page) -> None:
    """403 и «блока нет» — одно состояние: две роли без права видят одно и то же."""
    page = app_page("/webapp/admin/t/pending", headers=TROUPE)
    _settled(page)

    assert page.locator("#status").inner_text().strip() == DENIED
    assert "Заявок нет" not in _screen_text(page)
    assert page.svoi_errors == [], page.svoi_errors


def test_у_admin_ни_одной_главной_кнопки_в_списке(app_page) -> None:
    """А9: главная кнопка одна на экран, а не по одной на каждую заявку."""
    page = app_page("/webapp/admin/t/pending")
    _settled(page)

    assert "Новичок" in _screen_text(page), "стенд не отдал заявку — проверять нечего"
    assert page.locator("#pending-list .ui-btn--primary").count() == 0, \
        "в списке снова главные кнопки"
    accept = page.locator("#pending-list button", has_text="Принять")
    assert accept.count() == 1
    assert accept.first.is_visible()
    assert page.svoi_errors == [], page.svoi_errors


def test_без_права_согласования_строки_только_для_чтения(app_page) -> None:
    """`participants.manage` без `registration.approve`: список виден, кнопок нет.

    Раздел открывается любым из двух прав, а действие — только согласованием.
    Кнопка, которая ответит 403 без объяснения, — тот же дефект Б3.
    """
    page = app_page("/webapp/admin/t/pending")

    def manager_only(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=(
            '{"ok": true, "allowed": ["people", "pending"],'
            ' "actions": {"approve_pending": false},'
            ' "blocks": {"pending": [{"id": 3, "display_name": "Новичок",'
            ' "role": "troupe", "invite_code": "AB12CD34"}]}}'))

    page.route("**/webapp/admin/overview", manager_only)
    page.reload(wait_until="load")
    _settled(page)

    assert "Новичок" in _screen_text(page), "человек с правом на раздел потерял список"
    assert page.locator("#pending-list button").count() == 0, \
        "кнопки «Принять»/«Отклонить» нарисованы без права согласования"
    assert page.svoi_errors == [], page.svoi_errors
