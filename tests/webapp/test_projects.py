"""Экраны проектов в браузере: список, карточка, отказ по праву (E6-2).

Проверяется то, чего не видят общие замеры `test_miniapp_system.py` (он гоняет оба
адреса на 390/320 px — эмодзи, скролл, шрифт, цели 44 px): содержимое строки списка,
блоки карточки с `?id=`, права `master` на экране и строке «Управления».

Спецификация `docs/development/196` §12: каждый критерий закрывается отдельным тестом,
«Готово» без зелёного теста не считается. Отсюда две проверки — AC-42 и AC-43.

Обоснование: AC-42, AC-43, `docs/development/196-e6-projects-spec.md` §12, §13.
Данные стенда — `tests/webapp/server.py` (`PROJECTS`, `PROJECT_CARDS`): те же ключи,
что отдаёт `api/projects.py`, чтобы страница проверялась своим кодом, а не мокапом.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_projects.py -q
"""

from __future__ import annotations

import re

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")

#: Строка списка и блок «Деньги» карточки: сумма — неразрывные пробелы из `money()`.
PLAN_FACT = "план 120 000 ₽ · факт 86 400 ₽"
CARD_MONEY = "план 120 000 ₽ · факт 86 400 ₽ · остаток 33 600 ₽"

ORG = {"X-SVOI-Role": "org"}
MASTER = {"X-SVOI-Role": "master"}


def _text(node) -> str:
    """Текст с приведёнными пробелами: `money()` ставит неразрывные, сравнение — по смыслу."""
    return node.inner_text().replace("\u00a0", " ")


def _no_emoji(page, where: str) -> None:
    found = EMOJI.findall(page.inner_text("body"))
    assert not found, f"{where}: эмодзи в интерфейсе {found}"


# ---------------------------------------------------------------- AC-42: список
def test_список_проектов_с_планом_и_фактом(app_page) -> None:
    """AC-42: у `org` строка проекта несёт «план … · факт …» и ведёт в карточку.

    Кнопка «+ Проект» есть: право `budget.write` у организатора есть, и страница
    рисует её себе, а не прячет.
    """
    page = app_page("/webapp/projects", headers=ORG)
    page.wait_for_selector("#list .ui-row", timeout=10_000)

    rows = page.locator("#list .ui-row")
    assert rows.count() == 2, "в активном списке два проекта стенда"

    first = rows.first
    text = _text(first)
    assert "Спектакль «Чайка»" in text, text
    assert PLAN_FACT in text, text
    # Ближайшее событие — отдельной строкой, чтобы «что дальше» читалось без карточки.
    assert "ближайшее: сб Сб 14, 19:00 · репетиция" in text, text
    assert first.get_attribute("href") == "/webapp/project?id=1"

    # Плана и факта нет — прочерк, а не ноль: «не задан» и «ноль» разные ответы.
    second = _text(rows.nth(1))
    assert "план — · факт —" in second, second

    add = page.locator("#add-open")
    assert add.is_visible(), "у организатора нет кнопки «+ Проект»"
    assert "+ Проект" in _text(add)

    _no_emoji(page, "/webapp/projects")
    assert not page.svoi_errors, page.svoi_errors


def test_мастеру_список_проектов_не_открывается(app_page) -> None:
    """AC-42: у `master` нет ни строк, ни кнопки — экран отвечает словами отказа.

    Права `budget.read` у мастера нет: данные приходят 403, страница показывает фразу
    «Проекты видят администратор и организатор», а кнопку «+ Проект» прячет оболочка
    по `data-svoi-cap="budget.write"`. Отказ не должен выглядеть сбоем сети.
    """
    page = app_page("/webapp/projects", headers=MASTER)
    page.wait_for_selector("#error:not([hidden])", timeout=10_000)

    error = _text(page.locator("#error"))
    assert "Проекты видят администратор и организатор" in error, error
    assert "Не удалось" not in error, "403 подан как сбой сети"
    assert page.locator("#list .ui-row").count() == 0, "строки проектов у роли без права"
    assert page.locator("#add-open").is_hidden(), "кнопка «+ Проект» без права `budget.write`"

    _no_emoji(page, "/webapp/projects для мастера")
    assert not page.svoi_errors, page.svoi_errors


def test_строка_проектов_есть_в_управлении_у_организатора_и_нет_у_мастера(app_page) -> None:
    """AC-42: вход в раздел — строка «Проекты» в «Управлении», и она по праву.

    Строку рисует `/webapp/more` по ответу `/webapp/home/data` из реестра разделов
    (`SECTIONS`): у организатора она есть, у мастера её нет вовсе.
    """
    org = app_page("/webapp/more", headers=ORG)
    org.wait_for_load_state("networkidle")
    management = _text(org.locator("#management-list"))
    assert "Проекты" in management, management

    master = app_page("/webapp/more", headers=MASTER)
    master.wait_for_load_state("networkidle")
    management = _text(master.locator("#management-list"))
    assert "Проекты" not in management, management
    assert not master.svoi_errors, master.svoi_errors


# --------------------------------------------------------------- AC-43: карточка
def test_карточка_проекта_со_всеми_блоками(app_page) -> None:
    """AC-43: `/webapp/project?id=1` — блоки «Время», «Деньги», «Люди» на месте.

    Числа берутся из ответа карточки, ничего не считается на странице.
    """
    page = app_page("/webapp/project?id=1", headers=ORG)
    page.wait_for_selector("#card:not([hidden])", timeout=10_000)

    body = _text(page.locator("body"))
    for title in ("Время", "Деньги", "Люди"):
        assert title in body, f"нет блока «{title}»"

    assert _text(page.locator("#c-title")) == "Спектакль «Чайка»"
    assert "спектакль · активен · 2026-11-14 — …" in _text(page.locator("#c-meta"))
    assert "Ведущий: Саша Орлова" in _text(page.locator("#c-lead"))

    assert CARD_MONEY in _text(page.locator("#c-money"))
    assert "расходы 86 400 ₽ · доходы 0 ₽" in _text(page.locator("#c-more-money"))
    assert page.locator("#c-unlinked").is_hidden(), "у спектакля нет дохода вне событий"

    events = page.locator("#c-events .ui-row")
    assert events.count() == 2, "в блоке «Время» два события из ответа"
    assert "19:00 · Спектакль" in _text(events.first)
    assert "Зал на Обороны" in _text(events.first)
    assert page.locator("#c-events-empty").is_hidden(), "пустая фраза при непустом блоке"

    people = _text(page.locator("#c-people"))
    assert "Наташа — Саша Орлова" in people, people
    assert "ещё 6 участников событий" in people, people
    assert page.locator("#c-people-empty").is_hidden()

    # Право `budget.write` у организатора есть — кнопки правки на месте.
    assert page.locator("#c-edit").is_visible() and page.locator("#c-plan").is_visible()

    _no_emoji(page, "/webapp/project?id=1")
    assert not page.svoi_errors, page.svoi_errors


def test_пустые_блоки_карточки_дают_фразу(app_page) -> None:
    """AC-43: пустые блоки дают фразу, а не пустую подпись.

    Четыре фразы из §6.2: «Событий пока нет», «План не задан», «Операций нет»,
    «Состав не заполнен». Строки не исчезают — иначе «ноль» не отличить от «не считали».
    """
    page = app_page("/webapp/project?id=2", headers=ORG)
    page.wait_for_selector("#card:not([hidden])", timeout=10_000)

    money = _text(page.locator("#c-money"))
    assert "План не задан" in money, money
    assert "Операций нет" in money, money

    page.wait_for_selector("#c-events-empty:not([hidden])", timeout=10_000)
    assert _text(page.locator("#c-events-empty")) == "Событий пока нет"
    assert page.locator("#c-events .ui-row").count() == 0

    page.wait_for_selector("#c-people-empty:not([hidden])", timeout=10_000)
    assert _text(page.locator("#c-people-empty")) == "Состав не заполнен"
    assert page.locator("#c-people .ui-row").count() == 0

    _no_emoji(page, "/webapp/project?id=2")
    assert not page.svoi_errors, page.svoi_errors
