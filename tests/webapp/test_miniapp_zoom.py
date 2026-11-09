"""V-051: пинч-зум включён только на экране файла (docs/development/150 §2.8, §3 D3).

Проверяется поведение, а не разметка: мета читается **после** `load`, когда оболочка
`webapp-shell.js` уже отработала. Иначе тест зелёный на HTML, который оболочка тут же
перетирает, — это и есть исходный дефект (замер §2.8: `user-scalable=yes` в разметке
и щипок 1.00 под настоящей оболочкой).

Стенд отдаёт настоящие страницы (`PAGE_ROUTES` — те же константы `pages/*`) и настоящий
`webapp-shell.js` с диска: подмены оболочки в этих тестах нет.

Щипок — настоящий: `Input.synthesizePinchGesture` есть только в Chromium, поэтому
проверки масштаба на WebKit пропускаются (пинч через Playwright там не измерить).
Само окружение тоже решает: Linux-headless Chromium в CI отвечает на тот же вызов
масштабом 1.00, а macOS Chromium — 3.00. Чтобы красный CI не путали с дефектом
продукта, перед жестами ставится проба окружения на заведомо масштабируемой странице
(`_pinch_available`): если даже собранная тестом страница с `user-scalable=yes`
не увеличивается — окружение не умеет синтетический жест, и жесты скипаются
с этой причиной. Договор (одна мета и `user-scalable=no` у 30 экранов) проверяется
по всем 31 маршруту в обоих движках и от окружения не зависит.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_zoom.py -q --browser chromium
"""

from __future__ import annotations

import pytest

from tests.webapp.conftest import TELEGRAM_HASH
from tests.webapp.server import PAGE_ROUTES

FILE_ROUTE = "/webapp/file"
NOZOOM = "maximum-scale=1, user-scalable=no"
ZOOM = "maximum-scale=5, user-scalable=yes"
VIEWPORT = {"width": 390, "height": 844}

# Замер состояния страницы после загрузки: сколько мет, что в первой, есть ли флаг
# на <html> и каков масштаб visual viewport.
STATE = """() => {
  const metas = [...document.querySelectorAll('meta[name="viewport"]')].map(m => m.content || '');
  const vv = window.visualViewport;
  return {
    count: metas.length,
    content: metas[0] || '',
    zoom: document.documentElement.hasAttribute('data-svoi-zoom'),
    scale: Number((vv ? vv.scale : 1).toFixed(2)),
    scrollY: Math.round(window.scrollY),
    touch: getComputedStyle(document.body).touchAction,
  };
}"""


@pytest.fixture
def phone(browser):
    """Окно телефона 390×844 с тачем: в нём щипок и панорамирование настоящие."""
    context = browser.new_context(viewport=VIEWPORT, device_scale_factor=3,
                                  is_mobile=True, has_touch=True)
    yield context
    context.close()


def _open(context, base: str, path: str):
    page = context.new_page()
    page.goto(f"{base}{path}{TELEGRAM_HASH}", wait_until="load")
    try:
        page.wait_for_function("() => window.SVOI && window.SVOI.token", timeout=10_000)
    except Exception:  # pragma: no cover - мете токен не нужен, ждём ради стабильности
        pass
    return page


def _pinch(context, page, factor: float, x: int = 195, y: int = 380) -> None:
    session = context.new_cdp_session(page)
    session.send("Input.synthesizePinchGesture",
                 {"x": x, "y": y, "scaleFactor": factor,
                  "relativeSpeed": 800, "gestureSourceType": "touch"})
    page.wait_for_timeout(700)


PROBE_HTML = (
    '<!doctype html><html><head>'
    '<meta name="viewport" content="width=device-width, initial-scale=1, '
    'maximum-scale=5, user-scalable=yes"></head>'
    '<body style="height: 2000px">щипок</body></html>'
)


def _pinch_available(context) -> bool:
    """Умеет ли это окружение применить синтетический щипок к visualViewport.

    Проба идёт на странице, собранной самим тестом, с заведомо масштабируемой метой.
    Если даже она не увеличилась — дело не в продукте, а в среде: у WebKit нет
    CDP-сессии, а Linux-headless Chromium в CI отвечает на `synthesizePinchGesture`
    масштабом 1.00 (macOS Chromium — 3.00). Отдельная проба нужна, чтобы сломанный
    жест в продукте не прятался за «окружение не умеет»: проба страницы продукта
    идёт отдельно и падает, если мета верна, а увеличения нет.
    """
    probe = context.new_page()
    try:
        probe.goto("about:blank")
        probe.set_content(PROBE_HTML)
        try:
            _pinch(context, probe, 3.0, x=195, y=380)
        except Exception:  # pytest не должен падать на отсутствии API
            return False
        return bool(probe.evaluate(
            "() => (window.visualViewport ? visualViewport.scale : 1) > 1.5"))
    finally:
        probe.close()


@pytest.fixture
def pinch(phone):
    """Контекст, в котором синтетический щипок реально меняет масштаб."""
    if not _pinch_available(phone):
        pytest.skip("синтетический щипок не применяется в этом окружении: "
                    "Input.synthesizePinchGesture не меняет visualViewport.scale "
                    "(WebKit или Linux-headless Chromium); жест проверяется пробой "
                    "scripts/probes/pinch_probe_file.py и живым телефоном")
    return phone


@pytest.mark.parametrize("route", sorted(PAGE_ROUTES))
def test_мета_после_оболочки_увеличивает_только_файл(phone, webapp_server, route):
    """V-051, К2: одна мета у каждого экрана, отклоняется ровно `/webapp/file`.

    Ожидание не «31 экран» и не список из 30 адресов, а `route == FILE_ROUTE`:
    множество отклонившихся маршрутов получается `{FILE_ROUTE}` само, без правки
    теста при появлении нового экрана.
    """
    page = _open(phone, webapp_server, route)
    state = page.evaluate(STATE)
    page.close()

    assert state["count"] == 1, f"{route}: мет в документе {state['count']}: {state['content']}"
    if route == FILE_ROUTE:
        assert ZOOM in state["content"], state["content"]
        assert NOZOOM not in state["content"], state["content"]
        assert state["zoom"] is True, f"{route}: нет data-svoi-zoom на <html>"
    else:
        assert NOZOOM in state["content"], f"{route}: мета изменилась: {state['content']}"
        assert state["zoom"] is False, f"{route}: появился data-svoi-zoom"
    # touch-action: manipulation остаётся на всех экранах, включая файл: щипку
    # по спецификации он не мешает, а двойной тап по кнопкам гасит (D3.2).
    assert state["touch"] == "manipulation", f"{route}: touch-action {state['touch']}"


def test_щипок_увеличивает_файл_и_не_трогает_соседние(phone, pinch, webapp_server):
    """V-051, К3: щипок 3× даёт scale ≥ 2.0 на файле и ровно 1.00 на остальных."""
    cases = (f"{FILE_ROUTE}?id=3", "/webapp/files", "/webapp/week")
    measured = {}
    for path in cases:
        page = _open(phone, webapp_server, path)
        page.wait_for_timeout(300)
        assert page.evaluate(STATE)["scale"] == 1.0, f"{path}: не 1.0 до щипка"
        _pinch(phone, page, 3.0)
        measured[path] = page.evaluate(STATE)["scale"]
        page.close()

    assert measured[f"{FILE_ROUTE}?id=3"] >= 2.0, measured
    for path in ("/webapp/files", "/webapp/week"):
        assert abs(measured[path] - 1.0) <= 0.01, measured


def test_возврат_масштаба_и_кнопка_назад_на_файле(phone, pinch, webapp_server):
    """V-051, К4: обратный щипок возвращает экран, «‹» 44×44 и ведёт на `/webapp/files`."""
    page = _open(phone, webapp_server, f"{FILE_ROUTE}?id=3")
    page.wait_for_function(
        "() => { var i = document.querySelector('#scene img');"
        " return i && i.complete && i.naturalWidth > 0; }", timeout=10_000)
    assert page.evaluate(STATE)["scrollY"] == 0, "экран файла прокручен до щипка"

    _pinch(phone, page, 3.0)
    assert page.evaluate(STATE)["scale"] >= 2.0, "щипок не увеличил файл"

    # Точка обратного щипка — внутри видимой области: при 3× она 130×281 CSS px
    # (замер D3.5), и точка (195, 380) уже вне её — CDP отвечает «Position out of bounds».
    _pinch(phone, page, 0.33, x=65, y=140)
    restored = page.evaluate(STATE)
    assert abs(restored["scale"] - 1.0) <= 0.01, f"масштаб не вернулся: {restored['scale']}"
    assert restored["scrollY"] == 0, f"страница уехала: scrollY {restored['scrollY']}"

    back = page.locator("a.ui-appbar__back").bounding_box()
    assert back is not None, "нет кнопки «‹»"
    assert abs(back["width"] - 44) <= 1 and abs(back["height"] - 44) <= 1, back

    page.locator("a.ui-appbar__back").tap()
    page.wait_for_url("**/webapp/files**", timeout=10_000)
