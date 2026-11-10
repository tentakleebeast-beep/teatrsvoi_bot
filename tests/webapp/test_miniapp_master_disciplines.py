"""Дисциплина педагога в профиле: форма карточки, анкета и «Поиск окон» (V-037).

Браузерные проверки доказывают **форму**, а не права сервера: что нарисовано в
листе «Дисциплины педагога», что ушло в запрос, что лист не закрылся после
действия, и что предупреждение на экране — текст сервера, а не слова из скрипта.

Стенд `tests/webapp/server.py` отвечает по тем же правилам, что `api/admin.py`
после V-037: роль педагога выдаётся без дисциплин, снятие последней проходит с
предупреждением. Состояние стенда меняется здесь же:

    from tests.webapp import server as stand
    stand.DISCIPLINES_STATE[2] = ["music"]

Запуск:

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest \\
        tests/webapp/test_miniapp_master_disciplines.py -q --browser chromium
"""

from __future__ import annotations

import pytest

from tests.webapp import server as stand

MASTER = {"X-SVOI-Role": "master"}
TROUPE = {"X-SVOI-Role": "troupe"}

ADMIN_WARNING = ("У педагога нет дисциплины: тренинг он назначит, но без предмета — "
                 "в состав войдут все педагоги. Выберите дисциплину в карточке.")
SELF_WARNING = ("Дисциплина не выбрана: тренинг назначить можно, но без предмета — "
                "в состав войдут все педагоги. Поменять её может администратор.")


@pytest.fixture(autouse=True)
def _reset_stand(webapp_server) -> None:
    """Исходное состояние стенда: журналы и дисциплины пусты, роли как в ADMIN_OVERVIEW."""
    stand.DISCIPLINES_STATE.clear()
    stand.ROLES_STATE.clear()
    stand.ROLES_STATE.update({1: ["admin"], 2: ["troupe"], 30: ["troupe"]})
    stand.GRANT_EVENTS.clear()
    stand.DISCIPLINE_EVENTS.clear()


def _settled(page) -> None:
    page.wait_for_load_state("networkidle")


def _open_card(page, name: str = "Валерий") -> None:
    """Открыть карточку человека из списка «Люди»."""
    page.locator("#people-list .ui-row", has_text=name).first.click()
    page.wait_for_selector(".ui-sheet #discipline-add, .ui-sheet .ui-dl", timeout=10_000)


def _open_disciplines(page) -> None:
    page.locator(".ui-sheet button", has_text="Дисциплины педагога").first.click()
    # Лист рисует содержимое после ответа `/webapp/form-data`: ждём сами строки
    page.wait_for_function(
        "() => { const sheet = document.querySelector('.ui-sheet'); if (!sheet) return false;"
        " return !!sheet.querySelector('#discipline-add, .ui-row, .ui-empty'); }",
        timeout=10_000)


def test_нет_дисциплины_лист_показывает_предупреждение_и_список(app_page) -> None:
    """D2: пустой список — предупреждение, выпадающий список и «Добавить»."""
    stand.ROLES_STATE[2] = ["troupe", "master"]
    page = app_page("/webapp/admin/t/people")
    _settled(page)
    _open_card(page)
    _open_disciplines(page)

    sheet = page.locator(".ui-sheet")
    assert ADMIN_WARNING in sheet.inner_text()
    assert sheet.locator(".ui-alert--warn").count() >= 1
    select = sheet.locator("#discipline-select")
    assert select.count() == 1
    # В списке только то, чего у человека ещё нет: справочник из четырёх записей
    assert select.locator("option").count() == 5, "первый пункт — «— выберите —»"

    select.select_option("music")
    sheet.locator("#discipline-add").click()
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('.ui-sheet .ui-row'))"
        ".some((row) => row.textContent.indexOf('Музыка') >= 0)",
        timeout=10_000)

    bodies = [event for event in stand.DISCIPLINE_EVENTS]
    assert bodies and bodies[-1] == {"participant_id": 2, "discipline": "music",
                                     "enabled": True}, bodies
    # Лист остался открытым: вторую дисциплину ставят здесь же (D3)
    assert page.locator(".ui-sheet").count() == 1
    assert page.locator(".ui-sheet .ui-row", has_text="Музыка").count() == 1
    assert page.locator(".ui-sheet .ui-row", has_text="ведёт").count() == 1
    assert ADMIN_WARNING not in page.locator(".ui-sheet").inner_text()
    # Тост называет дисциплину, а не код
    assert "Музыка" in page.locator(".ui-toast").inner_text()
    assert "music" not in page.locator(".ui-toast").inner_text()
    assert page.svoi_errors == [], page.svoi_errors


def test_одна_дисциплина_лист_не_выше_400(app_page) -> None:
    """Одна дисциплина — одна строка и «Добавить ещё»; лист помещается на экран."""
    stand.DISCIPLINES_STATE[2] = ["music"]
    page = app_page("/webapp/admin/t/people")
    _settled(page)
    _open_card(page)
    _open_disciplines(page)

    sheet = page.locator(".ui-sheet")
    assert sheet.locator(".ui-row", has_text="Музыка").count() == 1
    assert sheet.locator("#discipline-add").inner_text().strip() == "Добавить ещё"
    assert ADMIN_WARNING not in sheet.inner_text()

    height = page.evaluate(
        "() => document.querySelector('.ui-sheet').getBoundingClientRect().height")
    assert height <= 400, f"лист {height} px выше 400 на 390×844"
    assert page.svoi_errors == [], page.svoi_errors


def test_все_четыре_дисциплины_поля_добавления_нет(app_page) -> None:
    """4 из 4: четыре строки, поля выбора нет — вместо него «Все дисциплины отмечены»."""
    stand.DISCIPLINES_STATE[2] = ["acting", "music", "dance", "speech"]
    page = app_page("/webapp/admin/t/people")
    _settled(page)
    _open_card(page)
    _open_disciplines(page)

    sheet = page.locator(".ui-sheet")
    assert sheet.locator(".ui-row", has_text="ведёт").count() == 4
    assert "Все дисциплины отмечены" in sheet.inner_text()
    assert sheet.locator("#discipline-select").count() == 0
    assert sheet.locator("#discipline-add").count() == 0
    assert page.svoi_errors == [], page.svoi_errors


def test_снятие_последней_просит_подтверждение(app_page) -> None:
    """D5: подтверждение в листе, затем `enabled: false` и состояние «нет»."""
    stand.DISCIPLINES_STATE[2] = ["music"]
    stand.ROLES_STATE[2] = ["troupe", "master"]
    page = app_page("/webapp/admin/t/people")
    _settled(page)
    _open_card(page)
    _open_disciplines(page)

    sheet = page.locator(".ui-sheet")
    sheet.locator(".ui-row button", has_text="Снять").click()
    confirm = sheet.locator("#discipline-confirm")
    assert confirm.count() == 1, "снятие последней без подтверждения"
    assert "не останется ни одной дисциплины" in confirm.inner_text()
    assert stand.DISCIPLINE_EVENTS == [], "запрос ушёл до подтверждения"

    confirm.locator("button", has_text="Снять дисциплину").click()
    page.wait_for_function(
        "() => document.querySelector('.ui-sheet .ui-alert--warn') && "
        "document.querySelector('.ui-sheet').innerText.indexOf('без предмета') >= 0",
        timeout=10_000)

    assert stand.DISCIPLINE_EVENTS[-1] == {"participant_id": 2, "discipline": "music",
                                          "enabled": False}
    assert ADMIN_WARNING in page.locator(".ui-sheet").inner_text()
    assert page.locator(".ui-sheet").count() == 1, "лист закрылся после снятия"
    assert page.svoi_errors == [], page.svoi_errors


def test_выдача_педагога_без_дисциплины_проходит(app_page) -> None:
    """D4: кнопка активна без отметок, запрос без `disciplines`, в карточке предупреждение."""
    page = app_page("/webapp/admin/t/people")
    _settled(page)
    _open_card(page)

    page.locator(".ui-sheet button", has_text="Выдать педагога").first.click()
    page.wait_for_selector(".ui-sheet .ui-btn--primary", timeout=10_000)
    issue = page.locator(".ui-sheet .ui-btn--primary", has_text="Выдать роль педагога")
    assert issue.is_enabled(), "кнопка выдачи заблокирована без дисциплины"
    assert "Без дисциплины роль выдаётся" in page.locator(".ui-sheet").inner_text()

    issue.click()
    page.wait_for_function("() => document.querySelectorAll('.ui-sheet').length === 0",
                           timeout=10_000)
    assert stand.GRANT_EVENTS[-1] == {"participant_id": 2, "role": "master",
                                      "enabled": True}

    # Карточка перечиталась: предупреждение видно словами сервера
    _open_card(page)
    assert ADMIN_WARNING in page.locator(".ui-sheet").inner_text()
    assert page.svoi_errors == [], page.svoi_errors


def test_без_права_кнопок_правки_нет(app_page) -> None:
    """Мастер видит карточку в чтении: кнопок правки нет и запросов не было."""
    stand.ROLES_STATE[2] = ["troupe", "master"]
    page = app_page("/webapp/admin/t/people", headers=MASTER)
    _settled(page)
    _open_card(page)

    text = page.locator(".ui-sheet").inner_text()
    assert "Дисциплины педагога" not in text
    assert "Выдать педагога" not in text
    assert stand.DISCIPLINE_EVENTS == []

    troupe = app_page("/webapp/admin/t/people", headers=TROUPE)
    _settled(troupe)
    assert "Раздел доступен организатору и администратору." in troupe.locator("#status").inner_text()
    assert page.svoi_errors == [], page.svoi_errors


def test_анкета_мастера_блок_под_сохранить(app_page) -> None:
    """D7: блок «Дисциплины» под `#save` и кнопка не сдвигается вверх."""
    stand.ROLES_STATE[30] = ["master"]
    master = app_page("/webapp", headers=MASTER)
    _settled(master)
    save_y_master = master.locator("#save").bounding_box()["y"]
    assert "Дисциплины" in master.locator(".ui-screen").inner_text()
    assert SELF_WARNING in master.locator(".ui-screen").inner_text()
    # Блок стоит ниже кнопки, а не над формой (замер дизайна §2.4)
    block_y = master.locator("#disciplines-section").bounding_box()["y"]
    assert block_y > save_y_master

    troupe = app_page("/webapp", headers=TROUPE)
    # Тот же человек без роли педагога: у труппы блока дисциплин нет
    stand.ROLES_STATE[30] = ["troupe"]
    troupe.reload(wait_until="load")
    _settled(troupe)
    assert "Дисциплины" not in troupe.locator(".ui-screen").inner_text()
    assert "Дисциплина не выбрана" not in troupe.locator(".ui-screen").inner_text()
    # Кнопка «Сохранить» на том же стенде у роли без блока стоит там же:
    # блок добавлен ниже кнопки, а не сдвинул форму (замер «до/после», §11.3-7)
    save_y_troupe = troupe.locator("#save").bounding_box()["y"]
    assert abs(save_y_troupe - save_y_master) < 1, (save_y_master, save_y_troupe)
    assert master.svoi_errors == [], master.svoi_errors


def test_поиск_окон_предупреждает_вместо_пустоты(app_page) -> None:
    """D7: мастер без дисциплин видит предупреждение, а не пустую секцию."""
    stand.ROLES_STATE[30] = ["master"]
    page = app_page("/webapp/slots", headers=MASTER)
    _settled(page)

    page.locator("#kinds .ui-chip", has_text="Тренинг").click()
    section = page.locator("#discipline-section")
    assert section.is_visible(), "секция дисциплины снова молча скрыта"
    assert page.locator("#discipline-warning").inner_text().strip() == SELF_WARNING
    assert page.locator("#disciplines .ui-chip").count() == 0, "чипы без дисциплин"
    assert page.svoi_errors == [], page.svoi_errors
