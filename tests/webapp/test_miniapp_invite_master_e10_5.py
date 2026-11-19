"""E10-5: роль «Педагог» в выпуске инвайт-кода — независимая проверка на стенде.

Обоснование: карта `t_68eceb6d` (эпик E10, шаг 5), `docs/development/176` §9.10.
Проверяются пять пунктов карты на настоящем стенде `tests/webapp/server.py`,
обоих движках и обеих ширинах 320/390 px:

1. три сегмента роли «Труппа · Организатор · Педагог», стартовый — «Труппа»;
2. выбор «Педагог» → «Выпустить код»: код в листе, строка «Роль: педагог»,
   код уходит в буфер обмена;
3. новый код попадает в «Активные коды» со словом «педагог» (не `master`);
4. на 320 и 390 px три подписи читаются целиком, вбок не едет, JS-ошибок нет;
5. регресс: выпуск кода труппе и организатору — как раньше.

Файл намеренно не переиспользует `test_miniapp_invites_roles.py`: это проверка по
карте, а не пересказ теста исполнителя. Абсолютных пикселей нет — и ожидание, и
факт берутся из замера страницы, поэтому тест верен и на macOS-шрифте, и на
шрифте ubuntu-latest в CI.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest \
        tests/webapp/test_miniapp_invite_master_e10_5.py -q --browser webkit
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from tests.webapp import server as stand

INVITES = "/webapp/admin/t/invites"
SEGMENTS = "#invite-role .ui-segmented__item"
HINT = "Дисциплину педагог выберет при регистрации."
ISSUED = stand.INVITE_CODE

#: Так Telegram передаёт данные Mini App веб-клиенту (см. tests/webapp/conftest.py).
INIT_DATA = ("user=%7B%22id%22%3A30%2C%22first_name%22%3A%22Test%22%7D"
             "&auth_date=1793000000&hash=testhash")
TELEGRAM_HASH = (f"#tgWebAppData={quote(INIT_DATA, safe='')}"
                 "&tgWebAppVersion=7.0&tgWebAppPlatform=desktop&tgWebAppThemeParams=%7B%7D")

#: Замер строки сегментов: ширина сегмента, где лежит его текст, сколько строк
#: занял текст, обрезка, позиция ряда внутри контейнера и переполнение страницы.
MEASURE = r"""
() => {
  const box = document.getElementById('invite-role');
  const boxRect = box.getBoundingClientRect();
  const cs = getComputedStyle(box);
  const innerRight = boxRect.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
  const innerLeft = boxRect.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
  const items = Array.from(box.querySelectorAll('.ui-segmented__item')).map((node) => {
    const rect = node.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(node);
    const textRects = Array.from(range.getClientRects()).filter((r) => r.width > 0);
    return {
      role: node.getAttribute('data-role'),
      label: node.textContent.trim(),
      pressed: node.getAttribute('aria-pressed'),
      on: node.classList.contains('ui-segmented__item--on'),
      width: rect.width,
      height: rect.height,
      left: rect.left,
      right: rect.right,
      textLeft: Math.min(...textRects.map((r) => r.left)),
      textRight: Math.max(...textRects.map((r) => r.right)),
      textLines: textRects.length,
      clipped: node.scrollWidth - node.clientWidth,
      font: getComputedStyle(node).fontSize
    };
  });
  const hint = document.getElementById('invite-hint');
  const hintRect = hint.getBoundingClientRect();
  return {
    items: items,
    innerLeft: innerLeft,
    innerRight: innerRight,
    inner: innerRight - innerLeft,
    hint: {hidden: hint.hidden, width: hintRect.width, height: hintRect.height,
           right: hintRect.right, clipped: hint.scrollWidth - hint.clientWidth},
    docOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    viewport: document.documentElement.clientWidth
  };
}
"""


def _open(browser, base: str, width: int = 390, height: int = 844, role: str | None = None):
    """Страница приглашений как в Telegram: свой контекст, буфер обмена разрешён."""
    headers = {"X-SVOI-Role": role} if role else {}
    options = {"viewport": {"width": width, "height": height},
               "extra_http_headers": headers}
    if browser.browser_type.name == "chromium":
        # WebKit не знает permission `clipboard-write` (`Unknown permission`) и
        # проверяется тем же путём, что и в бою: writeText, при отказе — execCommand.
        options["permissions"] = ["clipboard-read", "clipboard-write"]
    context = browser.new_context(**options)
    page = context.new_page()
    page.svoi_errors = []
    page.on("pageerror", lambda exc: page.svoi_errors.append(str(exc)))
    page.goto(f"{base}{INVITES}{TELEGRAM_HASH}", wait_until="load")
    page.wait_for_selector("#invite-new:not([hidden])", timeout=15_000)
    page.wait_for_load_state("networkidle")
    return context, page


def _select(page, role: str) -> None:
    page.locator(f'{SEGMENTS}[data-role="{role}"]').click()
    if role == "master":
        page.wait_for_selector("#invite-hint:not([hidden])", timeout=5_000)
    else:
        page.wait_for_selector("#invite-hint[hidden]", state="attached", timeout=5_000)


def _issue(page):
    """Нажать «Выпустить код» и дождаться листа; вернуть текст листа."""
    page.locator("#invite-issue").click()
    page.wait_for_selector(".ui-sheet", timeout=15_000)
    return page.locator(".ui-sheet").inner_text()


def _sheet_close(page) -> None:
    page.locator(".ui-sheet__actions .ui-btn", has_text="Закрыть").first.click()
    page.wait_for_selector(".ui-sheet", state="detached", timeout=5_000)


def _arm_copy_probe(page) -> None:
    """Запомнить, что экран отдал в буфер обмена (оба пути `copyText`)."""
    page.evaluate("""() => {
      window.__copied = [];
      if (navigator.clipboard && navigator.clipboard.writeText) {
        const original = navigator.clipboard.writeText.bind(navigator.clipboard);
        navigator.clipboard.writeText = (text) => {
          window.__copied.push(text);
          return original(text).catch(() => undefined);
        };
      }
      const exec = document.execCommand.bind(document);
      document.execCommand = (command, ...rest) => {
        if (command === 'copy') {
          const active = document.activeElement;
          window.__copied.push(active && 'value' in active ? active.value : '');
        }
        return exec(command, ...rest);
      };
    }""")


def test_пункт1_три_сегмента_и_стартовый_труппа(browser, webapp_server) -> None:
    """Пункт 1 карты: три сегмента, стартовый «Труппа», подсказки нет."""
    context, page = _open(browser, webapp_server)
    try:
        state = page.evaluate(MEASURE)
        assert [item["label"] for item in state["items"]] == ["Труппа", "Организатор", "Педагог"], state
        assert [item["role"] for item in state["items"]] == ["troupe", "org", "master"], state
        assert [item["on"] for item in state["items"]] == [True, False, False], state
        assert [item["pressed"] for item in state["items"]] == ["true", "false", "false"], state
        assert state["hint"]["hidden"] is True, "подсказка видна до выбора педагога"
        assert page.svoi_errors == [], page.svoi_errors
    finally:
        context.close()


def test_пункт2_выпуск_педагогу_код_роль_копирование(browser, webapp_server) -> None:
    """Пункт 2 карты: код появился, «Роль: педагог» в тексте, код копируется."""
    context, page = _open(browser, webapp_server)
    try:
        _arm_copy_probe(page)
        _select(page, "master")
        text = _issue(page)

        assert ISSUED in text, text
        assert "Роль: педагог." in text, text
        assert "master" not in text.lower(), f"в лист утекло машинное имя роли: {text}"
        assert page.locator(".ui-sheet__title").inner_text().strip() == "Код приглашения"

        page.locator(".ui-sheet__actions .ui-btn", has_text="Копировать").first.click()
        copied = page.evaluate("window.__copied")
        assert copied == [ISSUED], f"в буфер ушло {copied!r}, а не {ISSUED!r}"
        try:
            clipboard = page.evaluate("navigator.clipboard.readText()")
        except Exception:  # pragma: no cover — WebKit без доступа к системному буферу
            clipboard = None
        if clipboard is not None:
            assert clipboard == ISSUED, f"в буфере обмена {clipboard!r}"
        assert page.svoi_errors == [], page.svoi_errors
    finally:
        context.close()


def test_пункт3_новый_код_в_активных_с_ролью_педагог(browser, webapp_server) -> None:
    """Пункт 3 карты: в «Активных кодах» у нового кода роль «педагог».

    Стенд не хранит выданные коды: `ADMIN_OVERVIEW["blocks"]["invites"]` пуст, а
    POST `/webapp/admin/invite` его не меняет. Поэтому выданный код кладётся в
    блок перехватом запроса — ответ отдаёт настоящий стенд, меняется только то,
    что в бою сервер вернул бы из базы (`api/admin.py:1019`, `list_invites`).

    Форма блока — список, как в бою (`blocks["invites"] = list_invites(...)["data"]`),
    а не `{"data": [...]}`: стенд отдаёт именно обёртку, и экран рисует пустое
    состояние даже с непустым списком (находка E10-5, карта о стенде заведена).
    """
    context, page = _open(browser, webapp_server)
    save = stand.ADMIN_OVERVIEW["blocks"]["invites"]

    def remember(route):
        stand.ADMIN_OVERVIEW["blocks"]["invites"] = [
            {"code": ISSUED, "role": "master",
             "expires_at": "2026-11-26T12:00:00+04:00", "max_uses": 1, "uses": 0}]
        route.continue_()

    page.route("**/webapp/admin/invite", remember)
    try:
        _select(page, "master")
        _issue(page)
        _sheet_close(page)

        page.wait_for_selector("#invite-list .ui-row", timeout=10_000)
        rows = page.locator("#invite-list .ui-row")
        assert rows.count() == 1, rows.all_inner_texts()
        row = rows.first.inner_text()
        assert ISSUED in row, row
        meta = page.locator("#invite-list .ui-row__meta").first.inner_text()
        assert "педагог" in meta, f"в строке кода роль {meta!r}, а не «педагог»"
        assert "master" not in meta.lower(), f"в строку утекло машинное имя роли: {meta!r}"
        assert page.svoi_errors == [], page.svoi_errors
    finally:
        stand.ADMIN_OVERVIEW["blocks"]["invites"] = save
        context.close()


@pytest.mark.parametrize("width", [320, 390])
def test_пункт4_подписи_целиком_без_переноса(browser, webapp_server, width: int) -> None:
    """Пункт 4 карты: 320 и 390 px — подписи читаются целиком, вбок не едет.

    Проверяются оба состояния переключателя: ширина сегмента зависит от
    жирности выбранного. Ни одна проверка не называет абсолютных пикселей.
    """
    context, page = _open(browser, webapp_server, width=width, height=658 if width == 320 else 844)
    try:
        for chosen in ("troupe", "master"):
            if chosen == "master":
                _select(page, "master")
                page.wait_for_timeout(200)
            state = page.evaluate(MEASURE)
            where = f"{width} px, выбран «{chosen}», {page.context.browser.browser_type.name}"

            assert len(state["items"]) == 3, state
            for item in state["items"]:
                assert item["textLines"] == 1, (
                    f"{where}: подпись «{item['label']}» на {item['textLines']} строк")
                assert item["clipped"] <= 1, (
                    f"{where}: подпись «{item['label']}» обрезана на {item['clipped']} px")
                assert item["textLeft"] >= item["left"] - 1 and item["textRight"] <= item["right"] + 1, (
                    f"{where}: текст «{item['label']}» выходит за сегмент "
                    f"({item['textLeft']:.1f}..{item['textRight']:.1f} против "
                    f"{item['left']:.1f}..{item['right']:.1f})")
                assert item["right"] <= state["innerRight"] + 1, (
                    f"{where}: сегмент «{item['label']}» выходит за контейнер "
                    f"({item['right']:.1f} > {state['innerRight']:.1f}) — контейнер "
                    f"с `overflow: hidden` его подрежет")
                assert item["left"] >= state["innerLeft"] - 1, (
                    f"{where}: сегмент «{item['label']}» начинается левее контейнера")
                assert item["height"] >= 43.5, (
                    f"{where}: цель «{item['label']}» {item['height']:.1f} px")
            assert state["docOverflow"] <= 0, (
                f"{where}: страница шире экрана на {state['docOverflow']} px")
            if chosen == "master":
                assert state["hint"]["hidden"] is False, where
                assert state["hint"]["clipped"] <= 1, (
                    f"{where}: подсказка обрезана на {state['hint']['clipped']} px")
                assert state["hint"]["right"] <= state["viewport"] + 1, (
                    f"{where}: подсказка выходит за экран")
            assert page.svoi_errors == [], page.svoi_errors
            print(f"ЗАМЕР {where}: " + ", ".join(
                f"{item['label']}={item['width']:.1f}px/{item['font']}" for item in state["items"])
                + f", содержимое={state['inner']:.1f}px, подсказка={state['hint']['width']:.1f}px")
    finally:
        context.close()


def test_пункт5_регресс_труппа_и_организатор(browser, webapp_server) -> None:
    """Пункт 5 карты: выпуск кода труппе и организатору работает как раньше."""
    context, page = _open(browser, webapp_server)
    bodies: list[dict] = []
    page.on("request", lambda request: bodies.append(request.post_data_json)
            if request.method == "POST" and request.url.endswith("/webapp/admin/invite")
            else None)
    try:
        for role, word in (("troupe", "труппа"), ("org", "организатор")):
            _select(page, role)
            text = _issue(page)
            assert ISSUED in text, text
            assert f"Роль: {word}." in text, f"{role}: {text}"
            _sheet_close(page)
        assert bodies == [{"role": "troupe", "ttl_days": 7, "max_uses": 1},
                          {"role": "org", "ttl_days": 7, "max_uses": 1}], bodies
        assert page.svoi_errors == [], page.svoi_errors
    finally:
        context.close()
