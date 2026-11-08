"""Выдача роли «педагог» в Mini App: дисциплина выбирается до запроса (Э1, Г16).

Критерий карты Э1: «Given администратор, When выдаёт в карточке «Педагог» без
дисциплины, Then форма требует выбрать дисциплину и без выбора запрос не уходит».

Почему браузером: решение принимает скрипт `confirmMaster` (`pages/admin_disciplines.py`),
он гасит кнопку и не даёт `changeRole` собрать `POST /webapp/admin/grant` до выбора.
Проверка «в разметке кнопка есть» этот запрет не видит; серверный 400
`master_discipline_required` (tests/integration/test_master_role.py, Г3) — тоже:
он ловит уже отправленный запрос, а критерий требует, чтобы запрос не уходил вовсе.

Стенд: `tests/webapp/server.py` отдаёт карточку человека без роли педагога и пишет
все выдачи в журнал `GRANT_EVENTS`.

Спека роли: docs/architecture/140-master-role.md (Э1, критерий Г16).
"""

from __future__ import annotations


def _open_person_card(app_page):
    page = app_page("/webapp/admin/t/people")
    page.wait_for_selector("#people-list .ui-row")
    page.locator("#people-list .ui-row").first.click()
    page.get_by_role("button", name="Выдать педагога").wait_for()
    return page


def _grants(page) -> list[dict]:
    captured: list[dict] = []
    page.on("request", lambda request: captured.append(request.post_data_json)
            if request.url.endswith("/webapp/admin/grant") else None)
    return captured


def test_без_дисциплины_запрос_не_уходит(app_page) -> None:
    """Г16, docs/architecture/140-master-role.md: выдача без предмета не уходит."""
    page = _open_person_card(app_page)
    grants = _grants(page)

    page.get_by_role("button", name="Выдать педагога").click()
    issue = page.get_by_role("button", name="Выдать роль педагога")
    assert issue.is_disabled(), \
        "кнопка выдачи активна без дисциплины: сервер получит запрос без предмета"

    # Нажатие по погашенной кнопке не должно родить запрос: обработчик обязан
    # упереться в пустой выбор, а не отправить пустой список.
    try:
        issue.click(force=True, timeout=2_000)
    except Exception:
        pass
    page.wait_for_timeout(200)
    assert not grants, f"запрос ушёл без дисциплины: {grants}"
    assert not page.svoi_errors, page.svoi_errors


def test_выбранная_дисциплина_уезжает_в_запрос(app_page) -> None:
    """Г16, docs/architecture/140-master-role.md: выбранный предмет едет в запрос."""
    page = _open_person_card(app_page)
    grants = _grants(page)

    page.get_by_role("button", name="Выдать педагога").click()
    issue = page.get_by_role("button", name="Выдать роль педагога")
    assert issue.is_disabled()

    page.get_by_role("button", name="Музыка").click()
    assert not issue.is_disabled(), "после выбора дисциплины выдача осталась закрытой"
    issue.click()
    page.wait_for_selector(".ui-toast")

    assert grants, "выдача не отправила запрос"
    assert grants[0]["role"] == "master"
    assert grants[0]["enabled"] is True
    assert grants[0].get("disciplines") == ["music"], \
        f"дисциплина не уехала в запрос: {grants[0].get('disciplines')!r}"
    assert not page.svoi_errors, page.svoi_errors
