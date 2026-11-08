"""Главная Mini App: шиммер вместо прыжка (карта Э4, docs/development/148).

Обоснование: docs/development/148-miniapp-home-shimmer.md, критерии К1–К13 §5;
состояния §3 и разметка §4. Ответ владельца на V-047 (вариант «а») — в
docs/reports/74-questions.md: при 5xx и обрыве сообщение одно, его даёт баннер
оболочки, страница своё не дублирует.

Тесты проверяют поведение в браузере, а не строку в файле: первый кадр до ответа
данных, одну подмену содержимого, отсутствие сдвига шапки, состояния ошибки и
повтора. Ответ `/webapp/home/data` задерживается на 800 мс, как в замере дизайна;
задержка живёт в браузере (обёртка `fetch` из init script), чтобы тест мог
посмотреть на экран до прихода данных.

Запуск:

    SVOI_WEBAPP_TESTS=1 env -u PYTHONPATH .venv/bin/python -m pytest tests/webapp \\
        -q --browser chromium
"""

from __future__ import annotations

import json

import pytest

from tests.webapp.conftest import TELEGRAM_HASH, VIEWPORT

DATA_URL = "**/webapp/home/data"
DELAY_MS = 800
MESSAGE = "Сайт недоступен, попробуйте ещё раз."
RETRY = "Повторить"

# Роль по умолчанию — студиец без условных блоков (24 человека из 28): ни заявок,
# ни сегодняшнего события, ни долга, записи операций нет.
TROUPE = {
    "ok": True,
    "name": "Тестовая Студийка",
    "role": "troupe",
    "role_label": "студиец",
    "staff": False,
    "can_record": False,
    "week": {"title": "26 октября — 1 ноября", "marked": 0, "days": 7},
}

# Задержка ответа данных — в браузере, а не в обработчике маршрута: синхронный
# `time.sleep` заблокировал бы сам тест и первый кадр было бы нечем измерить.
SLOW_FETCH = """
(function () {
  var original = window.fetch;
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    if (url.indexOf("/webapp/home/data") === -1) {
      return original.call(window, input, init);
    }
    return new Promise(function (resolve) {
      setTimeout(function () { resolve(original.call(window, input, init)); }, %d);
    });
  };
})();
"""

# Наблюдатель на main: считает только мутации верхнего уровня (сам main), потому
# что перерисовка внутри старого контейнера — это и есть «прыжок», от которого
# уходим. Заодно ловит кадр, где имя человека и скелет видны одновременно.
OBSERVE = """
(function () {
  window.__homeMut = {removed: 0, added: 0, both: 0, started: false};
  function start() {
    var main = document.querySelector("main");
    if (!main || window.__homeMut.started) { return; }
    window.__homeMut.started = true;
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        if (record.target !== main) { return; }
        window.__homeMut.removed += record.removedNodes.length;
        window.__homeMut.added += record.addedNodes.length;
      });
      var skeleton = main.querySelector("#name .ui-skeleton");
      if (skeleton && main.innerText.indexOf("Тестовая Студийка") !== -1) {
        window.__homeMut.both += 1;
      }
    }).observe(main, {childList: true, subtree: true});
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, {once: true});
  } else { start(); }
})();
"""

CLS = """
(function () {
  window.__cls = 0;
  try {
    new PerformanceObserver(function (list) {
      list.getEntries().forEach(function (entry) {
        if (entry.hadRecentInput) { return; }
        window.__cls += entry.value;
      });
    }).observe({type: "layout-shift", buffered: true});
  } catch (error) { window.__cls = null; }
})();
"""

LOADING_JS = """
() => {
  const q = (s) => document.querySelector(s);
  const name = q("#name"), role = q("#role"), meta = q("#week-meta");
  const weekLink = q("#week a");
  const nameSkeleton = name ? name.querySelector(".ui-skeleton") : null;
  const roleSkeleton = role ? role.querySelector(".ui-skeleton") : null;
  const metaSkeleton = meta ? meta.querySelector(".ui-skeleton") : null;
  return {
    logo: ((q("#logo") || {}).textContent || "").trim(),
    nameText: name ? name.innerText.trim() : null,
    roleText: role ? role.innerText.trim() : null,
    nameSkeleton: !!nameSkeleton,
    roleSkeleton: !!roleSkeleton,
    metaSkeleton: !!metaSkeleton,
    nameSkeletonHeight: nameSkeleton ? nameSkeleton.getBoundingClientRect().height : 0,
    metaSkeletonHeight: metaSkeleton ? metaSkeleton.getBoundingClientRect().height : 0,
    week: !!q("#week"),
    weekTitle: ((q("#week .ui-section__title") || {}).textContent || "").trim(),
    weekHref: weekLink ? weekLink.getAttribute("href") : null,
    loading: !!q("#home-loading"),
    body: document.body.innerText,
    busy: q("main") ? q("main").getAttribute("aria-busy") : null,
  };
}
"""

READY_JS = """
() => {
  const q = (s) => document.querySelector(s);
  const meta = q("#week-meta");
  const blocks = ["attention", "today", "debt"].filter(function (id) {
    const node = document.getElementById(id);
    return node && !node.hidden;
  });
  return {
    logo: ((q("#logo") || {}).textContent || "").trim(),
    name: q("#name") ? q("#name").innerText.trim() : null,
    role: q("#role") ? q("#role").innerText.trim() : null,
    week: !!q("#week"),
    weekHref: q("#week a") ? q("#week a").getAttribute("href") : null,
    metaText: meta ? meta.innerText.trim() : null,
    metaHeight: meta ? meta.getBoundingClientRect().height : 0,
    weekHeight: q("#week") ? q("#week").getBoundingClientRect().height : 0,
    skeletons: document.querySelectorAll(".ui-skeleton").length,
    conditional: blocks,
    status: !!document.getElementById("status"),
    busy: q("main") ? q("main").getAttribute("aria-busy") : null,
  };
}
"""

ALERT_JS = """
() => {
  const box = document.querySelector("main .ui-alert");
  const button = box ? box.querySelector("button") : null;
  return {
    text: box ? box.innerText.trim() : null,
    button: button ? button.innerText.trim() : null,
    buttons: document.querySelectorAll("main .ui-alert button").length,
    loading: !!document.getElementById("home-loading"),
    skeletons: document.querySelectorAll(".ui-skeleton").length,
    week: !!document.getElementById("week"),
    status: !!document.getElementById("status"),
  };
}
"""

BARE_JS = """
() => {
  const message = "Сайт недоступен, попробуйте ещё раз.";
  const leaves = Array.from(document.querySelectorAll("body *")).filter(function (node) {
    return node.children.length === 0 && (node.textContent || "").trim() === message;
  });
  const buttons = Array.from(document.querySelectorAll("button")).filter(function (node) {
    return (node.textContent || "").trim() === "Повторить";
  });
  return {
    logo: ((document.getElementById("logo") || {}).textContent || "").trim(),
    name: !!document.getElementById("name"),
    role: !!document.getElementById("role"),
    week: !!document.getElementById("week"),
    weekHref: document.querySelector("#week a")
      ? document.querySelector("#week a").getAttribute("href") : null,
    loading: !!document.getElementById("home-loading"),
    skeletons: document.querySelectorAll(".ui-skeleton").length,
    alerts: document.querySelectorAll("main .ui-alert").length,
    retryButtons: buttons.length,
    messageNodes: leaves.length,
    busy: document.querySelector("main").getAttribute("aria-busy"),
  };
}
"""

A11Y_JS = """
() => {
  const main = document.querySelector("main");
  const status = Array.from(document.querySelectorAll('[role="status"]'));
  const skeletons = Array.from(document.querySelectorAll(".ui-skeleton"));
  return {
    busy: main.getAttribute("aria-busy"),
    statusCount: status.length,
    statusText: status.length ? status[0].textContent.trim() : null,
    skeletonsHidden: skeletons.every(function (node) {
      return node.getAttribute("aria-hidden") === "true";
    }),
  };
}
"""

A11Y_AFTER_JS = """
() => {
  const main = document.querySelector("main");
  const status = Array.from(document.querySelectorAll('[role="status"]'));
  return {
    busy: main.getAttribute("aria-busy"),
    statusText: status.length ? status[0].textContent.trim() : "",
    focus: document.activeElement ? document.activeElement.className : "",
  };
}
"""


def _json(payload: dict, status: int = 200):
    """Маршрут стенда: тот же ответ, но под контролем теста."""
    def handler(route):
        route.fulfill(status=status, content_type="application/json",
                      body=json.dumps(payload, ensure_ascii=False))
    return handler


def _slow(page, ms: int = DELAY_MS) -> None:
    """Отложить ответ данных в браузере на `ms` — тест успевает увидеть первый кадр."""
    page.add_init_script(SLOW_FETCH % ms)


def _reload(page) -> None:
    page.reload(wait_until="load")


def _loading(page) -> dict:
    return page.evaluate(LOADING_JS)


def _wait_ready(page, timeout: int = 5_000) -> None:
    page.wait_for_selector("#home-loading", state="detached", timeout=timeout)


def _open(browser, webapp_server, *, reduced_motion: str | None = None):
    """Свой контекст — когда нужен параметр, которого нет в общей фикстуре."""
    options = {"viewport": VIEWPORT}
    if reduced_motion:
        options["reduced_motion"] = reduced_motion
    context = browser.new_context(**options)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{webapp_server}/webapp/home{TELEGRAM_HASH}", wait_until="load")
    page.wait_for_function(
        "() => window.SVOI && (window.SVOI.token || !window.Telegram?.WebApp?.initData)",
        timeout=10_000)
    return context, page, errors


def _overflow(page) -> float:
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")


def test_первый_кадр_без_лжи(app_page):
    """К1: до ответа видны знак, скелет имени и роли и настоящая карточка недели."""
    page = app_page("/webapp/home")
    _slow(page)
    page.route(DATA_URL, _json(TROUPE))
    _reload(page)

    state = _loading(page)
    assert state["loading"], "нет загрузочного состояния"
    assert state["logo"] == "СВОИ", state["logo"]
    assert state["nameSkeleton"] and state["roleSkeleton"], "нет скелета шапки"
    assert state["nameText"] == "" and state["roleText"] == "", "строка шапки уже с текстом"
    assert state["week"] and state["weekTitle"] == "Занятость на неделе", state["weekTitle"]
    assert state["weekHref"] == "/webapp/week", state["weekHref"]
    assert state["metaSkeleton"], "в счётчике недели нет скелета"
    assert "Открывается в разделе" not in state["body"], "вернулась фраза про раздел"


def test_знак_не_подменяется(app_page):
    """К2: `#logo` — постоянный знак; его место не двигается ни на пиксель."""
    page = app_page("/webapp/home")
    _slow(page)
    page.route(DATA_URL, _json(TROUPE))
    _reload(page)

    before = page.locator("#logo").bounding_box()
    assert _loading(page)["logo"] == "СВОИ"

    _wait_ready(page)
    after = page.locator("#logo").bounding_box()
    assert page.locator("#logo").inner_text().strip() == "СВОИ"
    assert before and after
    for key in ("x", "y", "width", "height"):
        assert abs(before[key] - after[key]) <= 1, (key, before, after)


def test_знак_не_подменяется_при_ошибке(app_page):
    """К2: ошибка ответа тоже не трогает знак и его место."""
    page = app_page("/webapp/home")
    _slow(page)
    page.route(DATA_URL, _json({"ok": False}))
    _reload(page)

    before = page.locator("#logo").bounding_box()
    page.wait_for_selector("main .ui-alert", timeout=5_000)

    after = page.locator("#logo").bounding_box()
    assert page.locator("#logo").inner_text().strip() == "СВОИ"
    assert before and after
    for key in ("x", "y", "width", "height"):
        assert abs(before[key] - after[key]) <= 1, (key, before, after)


def test_одна_подмена_без_промежуточного_кадра(app_page):
    """К3: скелет заменяется одной операцией — два узла верхнего уровня, а не 20."""
    page = app_page("/webapp/home")
    page.add_init_script(OBSERVE)
    _slow(page)
    page.route(DATA_URL, _json(TROUPE))
    _reload(page)
    _wait_ready(page)

    state = page.evaluate("() => window.__homeMut")
    assert state["started"], "наблюдатель не встал на main"
    assert state["removed"] == 1, f"удалений верхнего уровня: {state}"
    assert state["added"] == 1, f"вставок верхнего уровня: {state}"
    assert state["both"] == 0, "имя человека и скелет были на экране одновременно"


def test_неделя_без_лжи_и_без_сжатия(app_page):
    """К4: скелет строки недели 19 ± 1 px; пустой счётчик высоту сохраняет."""
    page = app_page("/webapp/home")
    _slow(page)
    page.route(DATA_URL, _json(TROUPE))
    _reload(page)

    loading = _loading(page)
    assert 18 <= loading["metaSkeletonHeight"] <= 20, loading["metaSkeletonHeight"]
    assert "Открывается в разделе" not in loading["body"]
    assert loading["weekHref"] == "/webapp/week"
    height_before = page.locator("#week").bounding_box()["height"]

    _wait_ready(page)
    ready = page.evaluate(READY_JS)
    assert ready["metaText"] == "", ready["metaText"]
    assert 18 <= ready["metaHeight"] <= 20, ready["metaHeight"]
    assert abs(ready["weekHeight"] - height_before) <= 2, (height_before, ready["weekHeight"])


def test_пусто_не_тупик(app_page):
    """К5: без данных экран остаётся полным — знак, имя, роль, неделя с кнопкой."""
    page = app_page("/webapp/home")
    page.route(DATA_URL, _json(TROUPE))
    _reload(page)
    _wait_ready(page)

    ready = page.evaluate(READY_JS)
    assert ready["logo"] == "СВОИ"
    assert ready["name"] == TROUPE["name"], ready["name"]
    assert ready["role"] == TROUPE["role_label"], ready["role"]
    assert ready["week"] and ready["weekHref"] == "/webapp/week"
    assert ready["skeletons"] == 0, f"остались скелеты: {ready['skeletons']}"
    assert ready["conditional"] == [], ready["conditional"]
    assert ready["status"] is False, "в разметке Главной остался #status"


def test_ошибка_ответа_на_месте_скелета(app_page):
    """К6: `ok: false` — сообщение на месте скелета, карточка недели остаётся."""
    page = app_page("/webapp/home")
    page.route(DATA_URL, _json({"ok": False}))
    _reload(page)
    page.wait_for_selector("main .ui-alert", timeout=5_000)

    state = page.evaluate(ALERT_JS)
    assert state["text"].startswith("Не удалось загрузить. Потяните экран вниз"), state["text"]
    assert state["button"] == RETRY, state["button"]
    assert state["loading"] is False, "загрузочный контейнер остался"
    assert state["skeletons"] == 0, state["skeletons"]
    assert state["week"] is True, "карточка недели пропала из-за ошибки"
    assert state["status"] is False, "сообщение ушло в #status, а не на место скелета"


def test_нет_сессии(app_page):
    """К7: 401 — другое сообщение и никакого «Повторить»."""
    page = app_page("/webapp/home")
    page.route(DATA_URL, _json({"detail": "token_expired"}, status=401))
    _reload(page)
    page.wait_for_selector("main .ui-alert", timeout=5_000)

    state = page.evaluate(ALERT_JS)
    assert state["text"] == "Откройте приложение из бота — сессия не подтверждена."
    assert state["buttons"] == 0, "у отказа сессии есть кнопка повтора"
    assert state["skeletons"] == 0, state["skeletons"]


def test_повтор_возвращает_загрузку(app_page):
    """К8: «Повторить» — повторный запрос с тем же рендером, а не перезагрузка."""
    page = app_page("/webapp/home")
    calls: list[str] = []

    def handler(route):
        calls.append(route.request.url)
        payload = {"ok": False} if len(calls) == 1 else TROUPE
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(payload, ensure_ascii=False))

    _slow(page)
    page.route(DATA_URL, handler)
    _reload(page)
    page.wait_for_selector("main .ui-alert button", timeout=5_000)

    page.click("main .ui-alert button")
    state = _loading(page)
    assert state["loading"] is True, "не вернулось загрузочное состояние"
    assert state["busy"] == "true", state["busy"]
    assert page.locator("main .ui-alert").count() == 0, "алерт остался на время повтора"

    _wait_ready(page)
    assert page.locator("#name").inner_text().strip() == TROUPE["name"]
    assert len(calls) == 2, calls


@pytest.mark.parametrize("mode", ("503", "обрыв"))
def test_5xx_и_обрыв_дают_одно_сообщение(app_page, mode):
    """К9: сообщение одно — баннер оболочки; страница молчит и убирает скелеты."""
    page = app_page("/webapp/home")
    if mode == "503":
        page.route(DATA_URL, _json({"ok": False, "detail": "service unavailable"}, status=503))
    else:
        page.route(DATA_URL, lambda route: route.abort())
    _reload(page)
    page.wait_for_selector("#net-error", state="visible", timeout=10_000)
    page.wait_for_selector("#home-loading", state="detached", timeout=10_000)

    state = page.evaluate(BARE_JS)
    assert state["loading"] is False, "загрузочный контейнер остался"
    assert state["skeletons"] == 0, state["skeletons"]
    assert state["alerts"] == 0, "страница продублировала сообщение баннера"
    assert state["logo"] == "СВОИ", state["logo"]
    assert state["week"] is True and state["weekHref"] == "/webapp/week"
    assert state["busy"] == "false", state["busy"]
    assert state["name"] is False and state["role"] is False, "пустые строки-заглушки"
    assert state["retryButtons"] == 1, f"кнопок «Повторить»: {state['retryButtons']}"
    assert state["messageNodes"] == 1, f"сообщений на странице: {state['messageNodes']}"


def test_скринридер_и_aria_busy(app_page):
    """К10: `aria-busy` ведёт загрузку; объявление одно, скелеты скрыты от чтения."""
    page = app_page("/webapp/home")
    _slow(page)
    page.route(DATA_URL, _json(TROUPE))
    _reload(page)

    state = page.evaluate(A11Y_JS)
    assert state["busy"] == "true", state["busy"]
    assert state["statusCount"] == 1, state["statusCount"]
    assert state["statusText"] == "Загружаем данные", state["statusText"]
    assert state["skeletonsHidden"], "скелет читается скринридером"

    # Фокус — на вкладке: подмена содержимого не должна его перехватывать.
    page.focus(".ui-tabbar__item[aria-current]")
    _wait_ready(page)

    after = page.evaluate(A11Y_AFTER_JS)
    assert after["busy"] == "false", after["busy"]
    assert after["statusText"] == "", after["statusText"]
    assert "ui-tabbar__item" in after["focus"], after["focus"]


def test_щадящее_движение(browser, webapp_server):
    """К11: при `prefers-reduced-motion` нет ни шиммера, ни появления."""
    context, page, errors = _open(browser, webapp_server, reduced_motion="reduce")
    try:
        _slow(page)
        page.route(DATA_URL, _json(TROUPE))
        page.reload(wait_until="load")

        skeleton = page.evaluate(
            "() => getComputedStyle(document.querySelector('.ui-skeleton')).animationName")
        assert skeleton == "none", skeleton

        _wait_ready(page)
        fade = page.evaluate("""() => {
          const node = document.querySelector(".ui-fade-in");
          if (!node) { return null; }
          const style = getComputedStyle(node);
          return {name: style.animationName, opacity: style.opacity};
        }""")
        assert fade, "у готового содержимого нет класса появления"
        assert fade["name"] == "none", fade["name"]
        assert float(fade["opacity"]) == 1, fade["opacity"]
        assert not errors, errors
    finally:
        context.close()


def test_сдвиг_в_пределах_хорошо(app_page):
    """К12: CLS ≤ 0,1 — замер дизайна до работы давал 0,161."""
    page = app_page("/webapp/home")
    page.add_init_script(CLS)
    _slow(page)
    page.route(DATA_URL, _json(TROUPE))
    _reload(page)
    _wait_ready(page)
    page.wait_for_timeout(300)

    cls = page.evaluate("() => window.__cls")
    assert cls is not None, "браузер не отдал layout-shift"
    assert cls <= 0.1, f"CLS {cls}"


def test_нет_горизонтального_скролла(app_page):
    """К13: окно 390 × 844 — загрузка, готово и ошибка не шире экрана."""
    page = app_page("/webapp/home")
    _slow(page)
    page.route(DATA_URL, _json(TROUPE))
    _reload(page)
    assert _overflow(page) <= 1, f"загрузка: {_overflow(page)} px"

    _wait_ready(page)
    assert _overflow(page) <= 1, f"готово: {_overflow(page)} px"

    page.unroute(DATA_URL)
    page.route(DATA_URL, _json({"ok": False}))
    _reload(page)
    page.wait_for_selector("main .ui-alert", timeout=5_000)
    assert _overflow(page) <= 1, f"ошибка: {_overflow(page)} px"


def test_справочник_обновлён(app_page):
    """К15: в `/webapp/ui` есть знак и оба вида скелета."""
    page = app_page("/webapp/ui")
    body = page.content()
    assert "ui-logo" in body, "нет строки для знака"
    assert "ui-skeleton--line-title" in body, "нет скелета строки заголовка"
    assert "ui-skeleton--line-text" in body, "нет скелета строки текста"
    assert "СВОИ" in page.inner_text(".ui-logo")
