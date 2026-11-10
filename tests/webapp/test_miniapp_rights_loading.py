"""Права оболочки на вкладках: скелет вместо мигающих кнопок (карта t_f279a0eb).

Обоснование: FR-H9 (права решает сервер, оболочка только не показывает),
docs/development/86 §5, docs/architecture/25 §9.2; замечание владельца 10.11.2026
после выката v0.10.0 — «шиммер работает только на Главной, а на остальных
вкладках кнопки появляются и исчезают, сильнее всего на „Управлении“».

До карты экран рождался с готовыми кнопками, а оболочка прятала лишние после
ответа `/webapp/nav`: человек видел кнопку и через долю секунды — пустое место.
Теперь экран приходит с признаком `data-svoi-rights="pending"`, по которому CSS
(`webapp-ui.css`) держит элементы с `data-svoi-cap` скелетом того же вида и
тайминга, что полосы на Главной, а оболочка переводит признак в `ready` один раз
и только после того, как лишние элементы скрыты.

Тесты проверяют поведение в браузере, а не строку в файле: первый кадр до ответа
nav, ровно один переход состояния, набор действий до и после на «Управлении»,
недоступное право (кнопки нет ни в один момент) и отказ nav (сообщение и повтор,
а не вечный скелет). Ответ nav задерживается в браузере обёрткой `fetch` из init
script — так же, как задержка данных Главной в `test_miniapp_home_loading.py`.
Стенд отдаёт `caps` по роли (`X-SVOI-Role`).

    SVOI_WEBAPP_TESTS=1 env -u PYTHONPATH .venv/bin/python -m pytest tests/webapp \\
        -q --browser chromium
"""

from __future__ import annotations

import pytest

from tests.webapp.conftest import TELEGRAM_HASH

NAV_URL = "**/webapp/nav"
DELAY_MS = 800
FAIL_TEXT = "Не удалось проверить права. Кнопки могут оказаться недоступны."
RETRY = "Повторить"

# Роль на стенде задаётся заголовком: `participant` — студиец без `budget.write`
# и без `schedule.read.all` (tests/webapp/server.py:_caps_of).
PARTICIPANT = {"X-SVOI-Role": "participant"}

# Задержка ответа nav — в браузере, а не в обработчике маршрута: `time.sleep` в
# маршруте заблокировал бы сам тест и первый кадр было бы нечем измерить.
SLOW_NAV = """
(function () {
  var original = window.fetch;
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    if (url.indexOf("/webapp/nav") === -1) {
      return original.call(window, input, init);
    }
    return new Promise(function (resolve) {
      setTimeout(function () { resolve(original.call(window, input, init)); }, %d);
    });
  };
})();
"""

# Наблюдатель на признак загрузки и на скрытие помеченных правом элементов:
# по нему видно, что переход состояния был один, а скрытие по праву случилось до
# снятия скелета (значит кадра «кнопка есть — кнопки нет» не было). Признак
# читается из локальной переменной, а не из `<html>`: записи одного пакета
# приходят пачкой уже после того, как оболочка поставила `ready`, и чтение
# живого атрибута приписало бы скрытие к следующему состоянию.
WATCH = """
(function () {
  window.__svoiWatch = {states: [], hides: [], reveals: []};
  var root = document.documentElement;
  var current = (root && root.getAttribute("data-svoi-rights")) || "pending";
  window.__svoiWatch.states.push(current);
  new MutationObserver(function (records) {
    records.forEach(function (record) {
      var node = record.target;
      if (!node || node.nodeType !== 1) { return; }
      if (record.attributeName === "data-svoi-rights") {
        current = node.getAttribute("data-svoi-rights") || "";
        window.__svoiWatch.states.push(current);
        return;
      }
      if (record.attributeName !== "hidden") { return; }
      if (!node.hasAttribute("data-svoi-cap")) { return; }
      var mark = (node.id || node.className || node.tagName) + " @" + current;
      if (node.hidden) { window.__svoiWatch.hides.push(mark); }
      else { window.__svoiWatch.reveals.push(mark); }
    });
  }).observe(document, {attributes: true, subtree: true,
                        attributeFilter: ["hidden", "data-svoi-rights"]});
})();
"""

STATE_JS = """
() => ({
  rights: document.documentElement.getAttribute("data-svoi-rights"),
  states: window.__svoiWatch ? window.__svoiWatch.states : [],
  hides: window.__svoiWatch ? window.__svoiWatch.hides : [],
  reveals: window.__svoiWatch ? window.__svoiWatch.reveals : [],
})
"""

# Состояние элемента, зависящего от права: скелет или настоящая кнопка.
PROBE_JS = """
(selector) => {
  const node = document.querySelector(selector);
  if (!node) { return null; }
  const style = getComputedStyle(node);
  const box = node.getBoundingClientRect();
  const first = node.firstElementChild ? getComputedStyle(node.firstElementChild) : null;
  return {
    hidden: node.hasAttribute("hidden"),
    denied: node.getAttribute("data-svoi-denied") === "1",
    color: style.color,
    pointer: style.pointerEvents,
    animation: style.animationName,
    gradient: style.backgroundImage.indexOf("gradient") !== -1,
    visibility: style.visibility,
    display: style.display,
    childVisibility: first ? first.visibility : null,
    width: box.width,
    height: box.height,
    text: (node.innerText || "").trim().slice(0, 60),
    tag: node.tagName.toLowerCase(),
  };
}
"""

# Действия экрана: кнопки и строки-ссылки, которые человек видит и может нажать.
ACTIONS_JS = """
() => Array.from(document.querySelectorAll(".ui-screen .ui-btn, .ui-screen .ui-row"))
  .filter(function (node) {
    if (node.hidden) { return false; }
    const style = getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden";
  })
  .map(function (node) { return (node.innerText || "").trim().split("\\n")[0].slice(0, 60); })
"""

SCROLL_JS = """
() => document.documentElement.scrollWidth - document.documentElement.clientWidth
"""


def _json(payload: dict, status: int = 200):
    """Маршрут стенда под контролем теста: тот же ответ, но с нужным кодом."""
    import json

    def handler(route):
        route.fulfill(status=status, content_type="application/json",
                      body=json.dumps(payload, ensure_ascii=False))
    return handler


def _open(browser, server: str, path: str, *, width: int = 390, scheme: str = "light",
          headers: dict | None = None, slow: int | None = DELAY_MS):
    """Свой контекст: нужен размер экрана и задержка nav, которых нет в фикстуре."""
    context = browser.new_context(viewport={"width": width, "height": 844},
                                  color_scheme=scheme,
                                  extra_http_headers=headers or {})
    page = context.new_page()
    page.svoi_errors = []
    page.on("pageerror", lambda exc: page.svoi_errors.append(str(exc)))
    page.add_init_script(WATCH)
    if slow:
        page.add_init_script(SLOW_NAV % slow)
    page.goto(f"{server}{path}{TELEGRAM_HASH}", wait_until="load")
    return context, page


def _probe(page, selector: str) -> dict:
    state = page.evaluate(PROBE_JS, selector)
    assert state is not None, f"{selector}: элемента нет в разметке"
    return state


def _state(page) -> dict:
    return page.evaluate(STATE_JS)


def _wait_ready(page, timeout: int = 10_000) -> dict:
    page.wait_for_function(
        "() => document.documentElement.getAttribute('data-svoi-rights') === 'ready'",
        timeout=timeout)
    return _state(page)


def _assert_skeleton(state: dict, where: str) -> None:
    """Элемент с правом стоит скелетом: виден, но не кнопка и не нажимается."""
    assert not state["hidden"], f"{where}: элемент скрыт вместо скелета"
    assert state["width"] > 0 and state["height"] > 0, f"{where}: у скелета нет места"
    assert state["animation"] == "ui-shimmer", (where, state["animation"])
    assert state["gradient"], f"{where}: скелет без заливки"
    assert state["color"] in ("rgba(0, 0, 0, 0)", "transparent"), (where, state["color"])
    assert state["pointer"] == "none", f"{where}: скелет нажимается"
    # У кнопки с текстом детей нет: текст гасит прозрачный цвет (проверен выше).
    if state["childVisibility"] is not None:
        assert state["childVisibility"] == "hidden", f"{where}: текст кнопки уже виден"


def _assert_button(state: dict, where: str) -> None:
    """Элемент с правом — настоящая кнопка: не скелет и не скрыт."""
    assert not state["hidden"], f"{where}: кнопка осталась скрытой"
    assert not state["denied"], f"{where}: кнопка помечена как запрещённая"
    assert state["animation"] != "ui-shimmer", f"{where}: кнопка осталась скелетом"
    assert state["color"] not in ("rgba(0, 0, 0, 0)", "transparent"), (where, state["color"])
    assert state["pointer"] != "none", f"{where}: кнопка не нажимается"
    assert state["childVisibility"] != "hidden", f"{where}: текст кнопки не вернулся"


# Элементы, зависящие от права: вкладка, адрес элемента и что это за действие.
GATED = (
    ("/webapp/week", "#to-summary", "строка «Сводка занятости»"),
    ("/webapp/money", "#money-actions a[data-svoi-cap]", "кнопка «Записать операцию»"),
    ("/webapp/admin/t/people", "#people-dues", "кнопка «Взносы: тариф и начисление»"),
)


@pytest.mark.parametrize("path,selector,title", GATED)
def test_вкладка_стоит_скелетом_до_ответа_nav(browser, webapp_server, path, selector, title):
    """К1: до ответа `/webapp/nav` действие на вкладке — скелет, а не кнопка.

    Элемент остаётся в разметке (страница — публичная константа), но кнопкой не
    является: не нажимается (`pointer-events: none`), текст не читается, вместо
    него полоса того же вида, что на Главной.
    """
    context, page = _open(browser, webapp_server, path)
    try:
        if path == "/webapp/money":
            # Контейнер главного действия открывает скрипт страницы по данным:
            # ждём его, иначе скелета ещё нет вовсе (это не мигание, а загрузка).
            page.wait_for_function(
                "() => { const box = document.getElementById('money-actions');"
                " return box && !box.hidden; }", timeout=10_000)
        if path == "/webapp/admin/t/people":
            page.wait_for_function(
                "() => { const box = document.getElementById('people-dues');"
                " return box && !box.hidden; }", timeout=10_000)

        state = _state(page)
        assert state["rights"] == "pending", state["rights"]
        _assert_skeleton(_probe(page, selector), f"{path} {title}")
        assert not page.svoi_errors, page.svoi_errors
    finally:
        context.close()


@pytest.mark.parametrize("width", (390, 320))
@pytest.mark.parametrize("scheme", ("light", "dark"))
def test_скелет_на_экране_и_в_теме(browser, webapp_server, width, scheme):
    """К1, К5: скелет виден на 390 и 320 px в светлой и тёмной схеме; ничего не едет.

    Один компонент на все вкладки: тот же `ui-shimmer` и та же заливка, что у
    полос на Главной, поэтому отдельного «второго скелета» нет.
    """
    context, page = _open(browser, webapp_server, "/webapp/week", width=width, scheme=scheme)
    try:
        _assert_skeleton(_probe(page, "#to-summary"), f"week {width}px {scheme}")
        assert page.evaluate(SCROLL_JS) <= 1, "скелет шире экрана"

        state = _wait_ready(page)
        _assert_button(_probe(page, "#to-summary"), f"week после nav {width}px {scheme}")
        assert page.evaluate(SCROLL_JS) <= 1, "кнопка шире экрана"
        assert state["states"].count("ready") == 1, state["states"]
    finally:
        context.close()


@pytest.mark.parametrize("path,selector,title", GATED)
def test_кнопка_появляется_один_раз_и_не_исчезает(browser, webapp_server, path, selector, title):
    """К2: после ответа nav действие одно и оно не пропадает.

    Переход состояния ровно один (`pending` → `ready`), и ни один помеченный
    правом элемент не скрылся уже после ответа: именно это и было «миганием».
    """
    context, page = _open(browser, webapp_server, path)
    try:
        _wait_ready(page, timeout=15_000)
        page.wait_for_timeout(400)          # кадр, в котором кнопка могла бы исчезнуть
        _assert_button(_probe(page, selector), f"{path} {title}")

        state = _state(page)
        assert state["states"].count("ready") == 1, state["states"]
        late = [mark for mark in state["hides"] if mark.endswith("@ready")]
        assert not late, f"{path}: элемент скрылся после ответа nav: {late}"
        assert not page.svoi_errors, page.svoi_errors
    finally:
        context.close()


def test_управление_список_действий_до_и_после(browser, webapp_server):
    """К2: на «Управлении» набор действий до и после ответа nav одинаков.

    Экран вкладки «Управление» (`/webapp/more`) рисует разделы по данным, а не по
    правам, поэтому список видимых действий не имеет права сократиться после
    ответа nav — это и есть проверка «кнопки не исчезают».
    """
    context, page = _open(browser, webapp_server, "/webapp/more")
    try:
        page.wait_for_function(
            "() => { const list = document.getElementById('management-list');"
            " return list && list.children.length > 0; }", timeout=10_000)
        before = page.evaluate(ACTIONS_JS)
        assert _state(page)["rights"] == "pending", "nav ответил раньше кадра"

        state = _wait_ready(page)
        page.wait_for_timeout(400)
        after = page.evaluate(ACTIONS_JS)

        assert after == before, f"список действий изменился после nav:\n{before}\n{after}"
        assert before, "на «Управлении» нет ни одного действия — тест ничего не проверил"
        late = [mark for mark in state["hides"] if mark.endswith("@ready")]
        assert not late, f"элемент скрылся после ответа nav: {late}"
    finally:
        context.close()


def test_без_права_кнопки_нет_ни_в_один_момент(browser, webapp_server):
    """К2: недоступное действие не появляется даже на миг — сразу скелет, потом пусто.

    Студиец без `schedule.read.all`: строка «Сводка занятости» стоит скелетом до
    ответа nav, после ответа скрыта. Кадра, где она настоящая кнопка, нет.
    """
    context, page = _open(browser, webapp_server, "/webapp/week", headers=PARTICIPANT)
    try:
        _assert_skeleton(_probe(page, "#to-summary"), "week без права")
        state = _wait_ready(page)
        page.wait_for_timeout(400)

        row = _probe(page, "#to-summary")
        assert row["hidden"] and row["denied"], row
        assert state["states"].count("ready") == 1, state["states"]
        late = [mark for mark in state["hides"] if mark.endswith("@ready")]
        assert not late, f"строка скрылась после ответа nav: {late}"
    finally:
        context.close()


def _wait_ready_after_retry(page) -> dict:
    """Права после «Повторить»: повтор бывает и запросом на месте, и перезагрузкой.

    Баннер сети (5xx, обрыв) повторяет экран перезагрузкой, баннер проверки прав —
    тем же запросом без перезагрузки. Ждём признак в любом случае; контекст
    исполнения при перезагрузке сменяется, поэтому ожидание в цикле.
    """
    for _ in range(24):
        try:
            return _wait_ready(page, timeout=1_000)
        except Exception:          # noqa: BLE001 — перезагрузка сменила документ
            page.wait_for_timeout(250)
    raise AssertionError("права не приехали после «Повторить»")


@pytest.mark.parametrize("mode", ("503", "обрыв", "отказ"))
def test_отказ_nav_даёт_сообщение_и_повтор(browser, webapp_server, mode):
    """К4: отказ nav — сообщение и повтор, а не пустой экран и не вечный скелет.

    Пока права неизвестны, действие стоит скелетом; если nav не ответил, скелет
    снимается (право всё равно проверит сервер на вызове), а причину и кнопку
    «Повторить» даёт баннер оболочки — тот же, что при обрыве сети. Три отказа:
    5xx и обрыв (их ловит сетевой баннер) и ответ `ok: false` — его видит только
    проверка прав, и сообщение у неё своё.
    """
    context, page = _open(browser, webapp_server, "/webapp/week")
    try:
        if mode == "503":
            page.route(NAV_URL, _json({"ok": False}, status=503))
        elif mode == "обрыв":
            page.route(NAV_URL, lambda route: route.abort())
        else:
            page.route(NAV_URL, _json({"ok": False}))
        page.reload(wait_until="load")

        page.wait_for_selector("#net-error", state="visible", timeout=15_000)
        banner = page.inner_text("#net-error")
        assert RETRY in banner, banner

        state = _state(page)
        assert state["rights"] in ("error", "ready"), (
            f"вечный скелет при отказе nav: {state['rights']}")
        if mode == "отказ":
            # Ответ 200 с `ok: false` сетевой баннер не видит — сообщение даёт
            # проверка прав, и оно называет причину.
            assert FAIL_TEXT in banner, banner
        _assert_button(_probe(page, "#to-summary"), f"week после отказа nav ({mode})")

        # Повтор: снимаем отказ и нажимаем «Повторить» — права приезжают.
        page.unroute(NAV_URL)
        page.click("#net-error button")
        state = _wait_ready_after_retry(page)
        page.wait_for_timeout(300)
        _assert_button(_probe(page, "#to-summary"), "week после повтора")
        assert page.locator("#net-error").count() == 0, "сообщение осталось после повтора"
        assert state["states"].count("ready") >= 1, state["states"]
    finally:
        context.close()


def test_переход_между_вкладками_без_второго_мигания(browser, webapp_server):
    """К5 задания: переключение вкладок не даёт второго мигания.

    На новой вкладке признак загрузки проходит один раз (`pending` → `ready`), и
    ни одно действие не скрывается после ответа — мигать нечем.
    """
    context, page = _open(browser, webapp_server, "/webapp/week")
    try:
        _wait_ready(page)
        page.click('.ui-tabbar a[href="/webapp/money"]')
        page.wait_for_load_state("load")
        page.wait_for_function(
            "() => window.__svoiWatch && window.__svoiWatch.states.length > 0",
            timeout=15_000)

        state = _wait_ready(page, timeout=15_000)
        page.wait_for_timeout(400)
        _assert_button(_probe(page, "#money-actions a[data-svoi-cap]"), "деньги после перехода")
        assert state["states"].count("ready") == 1, state["states"]
        late = [mark for mark in state["hides"] if mark.endswith("@ready")]
        assert not late, f"действие скрылось после ответа nav: {late}"
        assert not page.svoi_errors, page.svoi_errors
    finally:
        context.close()
