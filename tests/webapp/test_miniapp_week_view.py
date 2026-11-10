"""«Время → Моя неделя»: состояние дней видно, сегодня выделен, действия на экране.

Проверки держат решения владельца V-028 (а, в, д), V-029 (а), V-030 (а) и V-048
(docs/reports/74): состояние дня читается цветом и словом, сегодняшний день
выделен, дублирующий сегмент «Моя неделя | Сводка» убран, «Заполнить по
шаблону» доступна без прокрутки на 390×844, подпись дня на сводке — в одну
строку, кнопка сводки не уходит под нижнюю панель.

V-048 (09.11.2026) поменял нижний блок: «Загрузить из общего листа» встала на
место «Сохранить шаблон недели», сам шаблон уехал ниже — в блок со сводкой и
очисткой.

Пересмотр V-048 (09.11.2026, док. 149 D8/D11/D12): кнопки «Отметить сегодня»
больше нет — её работу делает нажатие на строку сегодняшнего дня, а вторая
строка этой строки говорит «жмите». Экран стал короче на 64 px: замер 890 px
при экране 844, под нижней панелью остаётся только «Очистить неделю» и всего на
7 px. Подсказка стоит 0 px — строка дня 52 px до и после.

Формулировка подсказки сокращена 10.11.2026 по замеру: полной фразе нужно
180.5 px, а вторая строка сегодняшнего дня оставляет 123 px на 390 и 53 px на
320 — человек видел обрезку. Числа сняты в Linux-окружении CI (Chromium и WebKit
дают одинаковые метрики шрифта), потому что macOS-замер обещал запас, которого
в CI не было: «нажмите» занимает там 62 px и на 320 не влезало. Выбранное
«жмите» занимает 45.5 px — запас 7.5 px на 320 и 77.5 px на 390; на macOS
41.3 px против 71. К25 проверяет геометрию, а не `textContent`: обрезанный
текст тоже отдаёт полную строку.

Дату фиксируем часами браузера: стенд отдаёт неделю 26.10–01.11.2026, и
сегодняшний день в ней — воскресенье 1 ноября. Иначе тест зависел бы от дня
прогона.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import datetime as dt

import pytest

from tests.webapp.conftest import TELEGRAM_HASH
from tests.webapp.server import SHEET_LOADED, WEEK_EVENTS

VIEWPORT_HEIGHT = 844
FIXED_TIME = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)   # сегодня в фикстуре
TODAY_SLOT = 6                                                         # вс 01.11.2026
TODAY_ISO = "2026-11-01"
HINT = "жмите"


@pytest.fixture(autouse=True)
def _clean_stand() -> None:
    """Журнал и «загруженные из листа» дни — на каждый тест свои.

    Иначе порядок прогона влияет на результат: POST одного теста виден
    следующему, а `SHEET_LOADED` помечает сегодняшний день отмеченным.
    """
    WEEK_EVENTS.clear()
    SHEET_LOADED.clear()


@pytest.fixture
def week_page(app_page):
    """Страница недели с остановленными часами: дата одна на всех прогонах."""
    page = app_page("/webapp/week")
    page.clock.install(time=FIXED_TIME)
    page.reload(wait_until="load")
    page.wait_for_function(
        "() => document.querySelector('[data-day-slot=\"0\"]')"
        " && document.querySelector('[data-day-slot=\"0\"]').dataset.state === 'busy'",
        timeout=10_000)
    return page


def _visible_without_scroll(page, selector: str) -> None:
    box = page.locator(selector).first.bounding_box()
    assert box is not None, f"нет {selector}"
    tabbar = page.locator(".ui-tabbar").first.bounding_box()
    assert tabbar is not None, "нет нижней панели"
    assert box["y"] + box["height"] <= tabbar["y"], (
        f"{selector}: низ на {box['y'] + box['height']:.0f} px, "
        f"нижняя панель с {tabbar['y']:.0f} px — уходит под прокрутку")


def test_неделя_читается_без_прокрутки(week_page) -> None:
    page = week_page

    # Дублирующего переключателя нет: развилка осталась вкладкой «Время» внизу.
    assert page.locator('[role="tablist"]').count() == 0, "сегмент «Моя неделя | Сводка» вернулся"
    assert page.locator('.ui-tabbar__item[aria-current="page"]').inner_text().strip() == "Время"

    # Действия, которые владелец оставил в первом экране (V-048, пересмотр):
    # кнопки «Отметить сегодня» больше нет, «Сохранить шаблон недели» поднялось
    # выше линии сгиба.
    _visible_without_scroll(page, "#fill-template")
    _visible_without_scroll(page, "#load-from-sheet")
    _visible_without_scroll(page, "#save-template")

    # «Очистить неделю» — последняя строка и единственная, что уходит под панель,
    # да и то на 7 px: одного короткого движения хватает, чтобы она вышла целиком.
    page.locator("#clear-week").scroll_into_view_if_needed()
    assert page.locator("#clear-week").is_visible(), "#clear-week"


def test_страница_длиннее_экрана_после_снятия_кнопки(week_page) -> None:
    """Замер пересмотра V-048: кнопка снята, экран 890 px при 844.

    Тест фиксирует замер и не даёт тихо вернуть кнопку или добавить строку:
    расхождение больше 5 px — это другая разметка, а не шум.
    """
    height = week_page.evaluate("document.documentElement.scrollHeight")
    assert 870 <= height <= 900, f"страница {height} px — замер V-048 (890) изменился"


def test_клик_по_сегодняшней_строке_открывает_лист(week_page) -> None:
    """К16: работу снятой кнопки делает строка сегодняшнего дня.

    Нажатие открывает тот же лист дня и само ничего не пишет; запись уходит
    одним POST только после выбора варианта и закрытия листа.
    """
    page = week_page
    page.locator(f'[data-day-slot="{TODAY_SLOT}"]').click()
    page.wait_for_selector(".ui-sheet__actions .ui-btn", timeout=5_000)
    assert "Занятость" in page.locator(".ui-sheet__title").inner_text()
    assert [body for kind, body in WEEK_EVENTS if kind == "POST"] == [], \
        "лист открылся и сразу записал — вариант ещё не выбран"

    page.locator('#day-modes .ui-radio-row[data-mode="busy"]').click()
    page.locator(".ui-sheet__actions .ui-btn", has_text="Готово").click()
    page.wait_for_timeout(300)

    posts = [body for kind, body in WEEK_EVENTS if kind == "POST"]
    assert len(posts) == 1, f"ожидался ровно один POST, пришло {len(posts)}"
    assert posts[0]["start"] == "2026-10-26", posts[0]
    days = {item["day"]: item for item in posts[0]["days"]}
    assert days[TODAY_ISO]["status"] == "busy", days


def test_подсказка_в_строке_сегодня(app_page) -> None:
    """К17: пустая вторая строка сегодняшнего дня говорит, что строка нажимается.

    Подсказка занимает только пустое место: у отмеченного дня стоят часы,
    у пустых несегодняшних дней — пусто.
    """
    page = app_page("/webapp/week")
    page.clock.install(time=FIXED_TIME)
    page.reload(wait_until="load")
    page.wait_for_function(
        "() => document.querySelector('[data-day-slot=\"0\"]')"
        " && document.querySelector('[data-day-slot=\"0\"]').dataset.state === 'busy'",
        timeout=10_000)

    def meta(slot: int) -> str:
        return page.locator(f'[data-day-slot="{slot}"] .ui-row__meta').inner_text().strip()

    assert meta(TODAY_SLOT) == HINT, meta(TODAY_SLOT)
    assert meta(2) == "" and meta(5) == "", (meta(2), meta(5))

    # К18: подсказка занимает только пустое место и не стоит ни пикселя.
    # Точные числа, а не диапазон: строка дня 52 px, страница 890 px — те же,
    # что до подсказки. Допуск в 5–10 px пропустил бы рост разметки молча.
    row_height = page.locator(f'[data-day-slot="{TODAY_SLOT}"]').bounding_box()["height"]
    assert round(row_height, 1) == 52.0, f"строка сегодняшнего дня {row_height} px вместо 52"
    with_hint = page.evaluate("document.documentElement.scrollHeight")
    assert with_hint == 890, f"страница {with_hint} px с подсказкой вместо замера 890"

    # Замер без подсказки: убираем текст второй строки в DOM и перечитываем.
    page.eval_on_selector(
        f'[data-day-slot="{TODAY_SLOT}"] .ui-row__meta',
        "el => { el.textContent = ''; }")
    without_hint = page.evaluate("document.documentElement.scrollHeight")
    assert without_hint == with_hint, (
        f"подсказка добавила {without_hint - with_hint} px: без неё {without_hint}, "
        f"с ней {with_hint}")
    row_without = page.locator(f'[data-day-slot="{TODAY_SLOT}"]').bounding_box()["height"]
    assert round(row_without, 1) == 52.0, f"без подсказки строка {row_without} px вместо 52"

    # Второй проход: сегодняшний день отмечен — подсказки нет, стоят часы.
    SHEET_LOADED[TODAY_ISO] = "занят весь день"
    page.reload(wait_until="load")
    page.wait_for_function(
        "() => document.querySelector('[data-day-slot=\"6\"]')"
        " && document.querySelector('[data-day-slot=\"6\"]').dataset.state === 'busy'",
        timeout=10_000)
    assert meta(TODAY_SLOT) == "с 00:00 до 24:00", meta(TODAY_SLOT)
    assert HINT not in page.locator(f'[data-day-slot="{TODAY_SLOT}"]').inner_text()


@pytest.mark.parametrize("width, height", ((390, 844), (320, 693)))
def test_подсказка_видна_целиком_на_двух_ширинах(browser, webapp_server,
                                                 width, height) -> None:
    """К25: подсказка видна целиком, а не только в `textContent`.

    Обрезанный текст отдаёт в `textContent` полную строку, поэтому прежний К17
    (сравнение текста) дефект пропустил: длинной фразе нужно 180.5 px, а вторая
    строка сегодняшнего дня оставляет 123 px на 390 и 53 px на 320 — человек
    видел «нажмите…». Проверка геометрическая: `scrollWidth <= clientWidth`.
    Числа — Linux-окружение CI, где метрики шрифта шире macOS: «нажмите» там
    62 px при 53 px места, выбранное «жмите» — 45.5 px при тех же 53.
    """
    context = browser.new_context(viewport={"width": width, "height": height},
                                  color_scheme="light")
    try:
        page = context.new_page()
        page.goto(f"{webapp_server}/webapp/week{TELEGRAM_HASH}", wait_until="load")
        page.clock.install(time=FIXED_TIME)
        page.reload(wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-day-slot=\"0\"]')"
            " && document.querySelector('[data-day-slot=\"0\"]').dataset.state === 'busy'",
            timeout=10_000)
        measured = page.locator(f'[data-day-slot="{TODAY_SLOT}"]').evaluate(
            """row => {
                 const meta = row.querySelector('.ui-row__meta');
                 const flag = row.querySelector('.ui-day__today');
                 return {text: meta.textContent.trim(),
                         scroll: meta.scrollWidth, client: meta.clientWidth,
                         state: row.querySelector('.ui-day__state').textContent.trim(),
                         flag: !flag.hidden && getComputedStyle(flag).display !== 'none',
                         height: row.getBoundingClientRect().height};
               }""")
    finally:
        context.close()

    assert measured["text"] == HINT, (width, measured)
    assert measured["scroll"] <= measured["client"], (
        f"{width} px: подсказке нужно {measured['scroll']} px, "
        f"а вторая строка оставляет {measured['client']} px")
    assert measured["state"] == "не отмечено", (width, measured)
    assert measured["flag"] is True, f"{width} px: слово «сегодня» скрыто — {measured}"
    assert abs(measured["height"] - 52) <= 1, (
        f"{width} px: строка дня {measured['height']} px вместо 52")


def test_состояние_дня_видно_цветом_и_словом(week_page) -> None:
    page = week_page

    states = page.eval_on_selector_all(
        "[data-day-slot]", "rows => rows.map(row => row.dataset.state)")
    assert states == ["busy", "busy", "empty", "free", "busy", "empty", "empty"], states

    words = page.eval_on_selector_all(
        "[data-day-slot] .ui-day__state", "els => els.map(el => el.textContent.trim())")
    assert words == ["занят", "занят", "не отмечено", "свободен", "занят",
                     "не отмечено", "не отмечено"], words

    # Цвет фона у трёх состояний разный: состояние видно до чтения подписи.
    colors = page.eval_on_selector_all(
        "[data-day-slot]", "rows => rows.map(row => getComputedStyle(row).backgroundColor)")
    assert len({colors[0], colors[3], colors[2]}) == 3, colors

    # Полоса слева тоже отличается, а не только слово.
    bars = page.eval_on_selector_all(
        "[data-day-slot]", "rows => rows.map(row => getComputedStyle(row, '::before').backgroundColor)")
    assert len({bars[0], bars[3], bars[2]}) == 3, bars


def test_сегодняшний_день_выделен(week_page) -> None:
    page = week_page

    today = page.locator(".ui-row--today")
    assert today.count() == 1, "выделен не ровно один день"
    assert today.get_attribute("data-day-slot") == str(TODAY_SLOT)
    assert today.locator(".ui-day__today").is_visible(), "у сегодняшнего дня нет слова «сегодня»"
    assert "сегодня" in today.inner_text()

    # Рамка выделения действительно отрисована.
    shadow = today.evaluate("el => getComputedStyle(el).boxShadow")
    assert shadow and shadow != "none", shadow


def test_сводка_в_одну_строку_и_кнопка_видна(app_page) -> None:
    page = app_page("/webapp/week/summary")
    page.clock.install(time=FIXED_TIME)
    page.reload(wait_until="load")
    page.wait_for_function(
        "() => document.querySelector('[data-day-slot=\"0\"]')"
        " && document.querySelector('[data-day-slot=\"0\"] .ui-day__state').textContent"
        " && document.querySelector('[data-day-slot=\"0\"] .ui-day__state').textContent !== '…'",
        timeout=10_000)
    page.wait_for_timeout(400)

    assert page.locator('[role="tablist"]').count() == 0, "сегмент «Моя неделя | Сводка» вернулся"
    # Адрес входа несёт даты показанной недели (Ш-05), поэтому сравниваем префикс.
    _visible_without_scroll(page, 'a#to-slots')
    assert page.get_attribute("a#to-slots", "href").startswith("/webapp/slots?from=")

    # Подпись дня укладывается в одну строку, а не растягивает строку на две.
    single = page.eval_on_selector_all(
        "[data-day-slot] .ui-row__meta",
        "els => els.map(el => ({white: getComputedStyle(el).whiteSpace,"
        " h: el.getBoundingClientRect().height,"
        " line: parseFloat(getComputedStyle(el).lineHeight)}))")
    for item in single:
        assert item["white"] == "nowrap", item
        assert item["h"] <= item["line"] + 1.5, item

    height = page.evaluate("document.documentElement.scrollHeight")
    assert height <= VIEWPORT_HEIGHT, f"страница сводки {height} px при экране {VIEWPORT_HEIGHT} px"

    # Сегодняшний день выделен и здесь.
    assert page.locator(".ui-row--today").count() == 1
