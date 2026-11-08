"""Сверка листов по праву: без `budget.import` — слова вместо кнопки.

`POST /webapp/admin/reconcile` закрыт правом `budget.import`: оно есть у админа
и организатора, а у `troupe` и `master` его нет. Значит кнопка «Проверить» на
`/webapp/reconcile` не должна оставаться у роли без права — нажатие упиралось
в 403 без объяснения (отчёт 142 §3 строка 10, карта t_01784cc4).

Страница — публичная константа, поэтому признак `reconcile` приезжает из
`/webapp/nav` уже после входа, а текст отказа называет адресата: сверку ведёт
организатор (docs/architecture/25 §9.2 — решает сервер, страница лишь не
показывает кнопку). Стенд задаёт роль заголовком `X-SVOI-Role: participant`.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

PARTICIPANT = {"X-SVOI-Role": "participant"}


def _settled(page) -> None:
    """Ждём, пока страница получила ответ о правах (запросы после входа стихли)."""
    page.wait_for_load_state("networkidle")


def test_участник_не_видит_кнопку_сверки_и_читает_кто_её_ведёт(app_page) -> None:
    """Без `budget.import` кнопки нет, вместо неё — объяснение с адресатом.

    Кнопка остаётся в разметке (страница — публичная константа), но скрыта по
    признаку `reconcile` из `/webapp/nav`: человек без права не должен нажимать
    то, что ответит 403.
    """
    page = app_page("/webapp/reconcile", headers=PARTICIPANT)
    _settled(page)

    assert page.locator("#check").count() == 1, "кнопка исчезла из разметки"
    assert not page.locator("#check").is_visible(), (
        "участнику без budget.import показана кнопка «Проверить»")
    assert page.locator("#denied").is_visible(), "нет объяснения вместо кнопки"

    text = page.locator(".ui-screen").inner_text()
    assert "Сверку ведёт организатор" in text, text
    assert page.svoi_errors == [], page.svoi_errors


def test_организатор_видит_кнопку_сверки(app_page) -> None:
    """У организатора право есть: кнопка на месте, объяснения отказа нет."""
    page = app_page("/webapp/reconcile")
    _settled(page)

    assert page.locator("#check").is_visible(), "организатор потерял кнопку «Проверить»"
    assert not page.locator("#denied").is_visible()
    assert page.svoi_errors == [], page.svoi_errors


def test_отказ_403_на_самом_вызове_объясняет_право_а_не_сеть(app_page) -> None:
    """Право сняли после входа — экран говорит о правах, а не «повторите через минуту».

    Признак `reconcile` подменён на «можно» перехватом `/webapp/nav`: страница
    показывает кнопку, а вызов получает 403 (у участника права нет). Отказ должен
    привести к тому же объяснению, что и на входе.
    """
    page = app_page("/webapp/reconcile", headers=PARTICIPANT)

    def allow(route) -> None:
        route.fulfill(status=200, content_type="application/json",
                      body='{"ok": true, "staff": false, "summary": false, "reconcile": true}')

    page.route("**/webapp/nav", allow)
    page.reload(wait_until="load")
    page.wait_for_function("() => window.SVOI && !!window.SVOI.token", timeout=10_000)
    _settled(page)

    assert page.locator("#check").is_visible(), "стенд не отдал ложное «можно»"
    page.locator("#check").click()
    page.wait_for_function("() => !document.getElementById('denied').hidden", timeout=10_000)

    text = page.locator(".ui-screen").inner_text()
    assert "Сверку ведёт организатор" in text, text
    assert "ещё раз через минуту" not in text, f"экран врёт про повтор: {text}"
