"""Карточка события Mini App: одна карточка на людское и на событие бота (190 §3).

Проверки держат AC-5…AC-8 документа `docs/development/190` и дизайн 187 §3:
тип показан плашкой цвета типа, название — без метки `[С]`, пустое поле не
оставляет пустой подписи, у людского события кнопки «Перенести» нет и сказано
словами, кто правит, экран держится на 320 px и не пишет JS-ошибок.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

OVERFLOW = ("() => document.documentElement.scrollWidth "
            "- document.documentElement.clientWidth")


def test_карточка_бота_рисует_тип_название_время_место_и_описание(app_page) -> None:
    """Событие бота: плашка типа с цветом, название без метки, все поля на месте.

    Метка `[С]` — служебная: человек видит «Спектакль» плашкой и «Жизнь
    насекомых» заголовком, а не строку `[С] Жизнь насекомых` (I-3, 190 §3.2).
    """
    page = app_page("/webapp/event?calendar=draft&event_id=bot-1")
    page.wait_for_selector("#card:not([hidden])", timeout=10_000)

    assert page.locator("#type").inner_text() == "Спектакль"
    badge_class = page.locator("#type").get_attribute("class") or ""
    assert "ev-color-11" in badge_class, f"плашка не окрашена цветом типа: {badge_class}"

    title = page.locator("#title").inner_text()
    assert title == "Жизнь насекомых", title
    assert "[С]" not in title, f"метка осталась в названии: {title}"

    rows = page.locator("#rows").inner_text()
    assert "2026-11-24" in rows and "вторник" in rows, rows
    assert "19:00–21:30" in rows, rows
    assert "Сцена" in rows, rows
    assert "Мария" in rows, rows
    assert page.locator("#description").inner_text() == "Сбор за час до начала"
    assert page.locator("#description").is_visible(), "описание не показано"

    assert page.locator("#move").is_visible(), "создатель потерял кнопку «Перенести»"
    assert page.locator("#why").is_hidden(), "создателю показана причина отказа"
    assert page.svoi_errors == [], page.svoi_errors


def test_людское_событие_без_кнопки_переноса_и_с_причиной(app_page) -> None:
    """Событие из Google: тип «Без типа», кнопки нет, причина названа словами.

    446 людских событий календаря строки `event_plan` не имеют — карточка
    открывается ключом Google, а не `plan_id` (190 §3.1).
    """
    page = app_page("/webapp/event?calendar=draft&event_id=human-1")
    page.wait_for_selector("#card:not([hidden])", timeout=10_000)

    assert page.locator("#type").inner_text() == "Без типа"
    badge_class = page.locator("#type").get_attribute("class") or ""
    assert "ev-color-" not in badge_class, f"у события без типа появился цвет: {badge_class}"

    assert page.locator("#move").is_hidden(), "людскому событию показали «Перенести»"
    assert page.locator("#why").is_visible(), "нет объяснения, почему переноса нет"
    assert "Событие создано в Google" in page.locator("#why-text").inner_text()
    assert page.svoi_errors == [], page.svoi_errors


def test_в_боевом_календаре_переноса_нет_ни_у_кого(app_page) -> None:
    """`calendar=read` — всегда причина `live_calendar`, даже у своего события (AC-7)."""
    page = app_page("/webapp/event?calendar=read&event_id=bot-1")
    page.wait_for_selector("#card:not([hidden])", timeout=10_000)

    assert page.locator("#move").is_hidden(), "в боевом календаре осталась кнопка переноса"
    assert "боевом календаре" in page.locator("#why-text").inner_text()


def test_пустые_поля_не_оставляют_пустых_строк(app_page) -> None:
    """Место, создатель, цвет и описание пусты — строк нет вовсе (AC-8).

    Строка с пустым значением читается как «здесь что-то есть, но не загрузилось»:
    экран не рисует подпись, у которой нет значения.
    """
    page = app_page("/webapp/event?calendar=draft&event_id=human-1")
    page.wait_for_selector("#card:not([hidden])", timeout=10_000)

    for row in ("row-place", "row-creator", "row-color"):
        assert page.locator(f"#{row}").is_hidden(), f"пустая строка {row} осталась на экране"
    assert page.locator("#description").is_hidden(), "пустое описание оставило подпись"

    visible = page.eval_on_selector_all(
        "#rows .ui-row",
        "nodes => nodes.filter(n => !n.hidden).map(n => n.id)")
    assert visible == ["row-date", "row-time"], f"на экране лишние строки: {visible}"


def test_событие_на_весь_день_без_строки_времени(app_page) -> None:
    """`all_day`: время `null` — строки «Время» нет, дата осталась (AC-8)."""
    page = app_page("/webapp/event?calendar=draft&event_id=allday-1")
    page.wait_for_selector("#card:not([hidden])", timeout=10_000)

    assert page.locator("#row-time").is_hidden(), "у события на весь день строка времени"
    assert page.locator("#row-date").is_visible(), "потерялась дата события"


def test_карточка_на_320_px_без_горизонтальной_прокрутки(app_page) -> None:
    """Самый узкий экран: карточка не шире 320 px и JS-ошибок нет."""
    page = app_page("/webapp/event?calendar=draft&event_id=bot-1")
    page.set_viewport_size({"width": 320, "height": 693})
    page.wait_for_selector("#card:not([hidden])", timeout=10_000)

    assert page.evaluate(OVERFLOW) <= 0, "карточка едет вбок на 320 px"
    assert page.svoi_errors == [], page.svoi_errors
