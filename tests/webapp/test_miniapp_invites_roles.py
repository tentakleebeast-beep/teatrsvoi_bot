"""Роль «Педагог» в выпуске инвайт-кода из Mini App: браузерный сценарий.

Обоснование: `docs/development/176` §9.10 (AC-5…AC-10), карта `t_ff6144fc`, эпик E10.
Проверяется то, что видит администратор: три сегмента роли с подписями «Труппа ·
Организатор · Педагог», подсказка о дисциплине только у педагога, выпуск кода
педагогу (тело запроса и лист) и отсутствие переключателя у роли без права.

Отдельно — узкая ширина: на 320 px три подписи должны читаться целиком и не
переноситься (проба E10-1 дала 70 / 110 / 74 px). Ширины проверяются с допуском
±1 px: `flex: 1` с `min-width: auto` — правило браузера, и другой движок округлит
иначе (§9.12).

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_invites_roles.py \\
        -q --browser chromium
"""

from __future__ import annotations

INVITES = "/webapp/admin/t/invites"
HINT = "Дисциплину педагог выберет при регистрации."
SHEET_ROLE = "Роль: педагог. Действует 7 дней, вход один."
DENIED = "Раздел доступен организатору и администратору."
CODE = "PED7AGK2"

SEGMENTS = "#invite-role .ui-segmented__item"

#: Замер сегмента: ширина, высота цели, обрезка и число строк подписи.
MEASURE = """
() => {
  const box = document.getElementById('invite-role');
  const style = window.getComputedStyle(box);
  const items = Array.from(box.querySelectorAll('.ui-segmented__item')).map((node) => {
    const rect = node.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(node);
    return {
      role: node.getAttribute('data-role'),
      label: node.textContent.trim(),
      pressed: node.getAttribute('aria-pressed'),
      on: node.classList.contains('ui-segmented__item--on'),
      width: rect.width,
      height: rect.height,
      left: rect.left,
      right: rect.right,
      clipped: node.scrollWidth - node.clientWidth,
      lines: range.getClientRects().length
    };
  });
  const card = document.getElementById('invite-new').getBoundingClientRect();
  return {
    items: items,
    inner: box.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
    card: {left: card.left, right: card.right},
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    viewport: document.documentElement.clientWidth
  };
}
"""


def _open(page, path: str = INVITES, headers: dict | None = None):
    """Экран приглашений с дождавшиеся ответами панели."""
    page = page(path, headers=headers) if headers else page(path)
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#invite-new:not([hidden])", timeout=10_000)
    return page


def _is_hint_visible(page) -> bool:
    return page.locator("#invite-hint").is_visible()


def test_три_сегмента_подписи_и_умолчание(app_page) -> None:
    """AC-5: три сегмента, подписи по порядку, выбранная — труппа, подсказки нет."""
    page = _open(app_page)

    items = page.locator(SEGMENTS)
    assert items.count() == 3, f"сегментов роли {items.count()}, а не три"
    labels = [items.nth(i).inner_text().strip() for i in range(3)]
    assert labels == ["Труппа", "Организатор", "Педагог"], labels
    assert [items.nth(i).get_attribute("data-role") for i in range(3)] == \
        ["troupe", "org", "master"]
    assert [items.nth(i).get_attribute("aria-pressed") for i in range(3)] == \
        ["true", "false", "false"]
    on = [items.nth(i).get_attribute("data-role") for i in range(3)
          if "ui-segmented__item--on" in (items.nth(i).get_attribute("class") or "")]
    assert on == ["troupe"], f"выбрано не по умолчанию: {on}"
    assert not _is_hint_visible(page), "подсказка видна до выбора педагога"
    assert page.svoi_errors == [], page.svoi_errors


def test_подсказка_только_у_педагога(app_page) -> None:
    """AC-6: «Педагог» показывает подсказку, возврат к «Труппе» — прячет."""
    page = _open(app_page)

    page.locator(f'{SEGMENTS}[data-role="master"]').click()
    page.wait_for_selector("#invite-hint:not([hidden])", timeout=5_000)
    state = page.evaluate(MEASURE)
    assert [item["pressed"] for item in state["items"]] == ["false", "false", "true"]
    assert [item["on"] for item in state["items"]] == [False, False, True]
    assert page.locator("#invite-hint").inner_text().strip() == HINT
    assert len(HINT) == 43

    page.locator(f'{SEGMENTS}[data-role="troupe"]').click()
    page.wait_for_selector("#invite-hint[hidden]", state="attached", timeout=5_000)
    state = page.evaluate(MEASURE)
    assert [item["pressed"] for item in state["items"]] == ["true", "false", "false"]
    assert [item["on"] for item in state["items"]] == [True, False, False]
    assert not _is_hint_visible(page), "подсказка осталась после возврата к труппе"
    assert page.svoi_errors == [], page.svoi_errors


def test_выпуск_кода_педагогу_тело_и_лист(app_page) -> None:
    """AC-7: телом уходит `master`, в листе — код и строка роли словами `ROLE`.

    Тело сравнивается распаренным JSON, а не подстрокой: иначе «role: master»
    прошло бы и при лишних полях, которые сервер молча примет.
    """
    page = _open(app_page)
    bodies: list[dict] = []
    page.on("request", lambda request: bodies.append(request.post_data_json)
            if request.method == "POST" and request.url.endswith("/webapp/admin/invite")
            else None)

    page.locator(f'{SEGMENTS}[data-role="master"]').click()
    page.locator("#invite-issue").click()
    page.wait_for_selector(".ui-sheet", timeout=10_000)

    assert bodies == [{"role": "master", "ttl_days": 7, "max_uses": 1}], bodies
    assert page.locator(".ui-sheet__title").inner_text().strip() == "Код приглашения"
    body = page.locator(".ui-sheet__body").inner_text()
    assert CODE in body, body
    assert SHEET_ROLE in body, body
    assert "Дисциплину педагог выберет при регистрации." not in body, \
        "подсказка попала в лист: там место роли и срока"
    assert page.svoi_errors == [], page.svoi_errors


def test_org_не_видит_сегментов_роли(app_page) -> None:
    """AC-8: у организатора формы нет — ни сегментов, ни подписей ролей.

    Кнопку добавляли в блок, который отдаётся скрытым; ошибка «переключатель
    вынесли из `#invite-new`» иначе прошла бы незамеченной: у org появлялся бы
    выбор роли без права выпуска.
    """
    page = app_page(INVITES, headers={"X-SVOI-Role": "org"})
    page.wait_for_load_state("networkidle")

    assert not page.locator("#invite-role").is_visible(), "у org виден переключатель роли"
    for index in range(page.locator(SEGMENTS).count()):
        assert not page.locator(SEGMENTS).nth(index).is_visible()
    text = page.locator(".ui-screen").inner_text()
    assert "Труппа" not in text and "Педагог" not in text, text
    assert page.locator("#status").inner_text().strip() == DENIED
    assert page.svoi_errors == [], page.svoi_errors


def _fits(state: dict, expected: list[int]) -> None:
    """Подписи не обрезаны, по одной строке, цель ≥ 44 px, ширины по ожиданию ±1."""
    assert len(state["items"]) == 3, state
    for item in state["items"]:
        assert item["clipped"] <= 1, f"подпись «{item['label']}» обрезана на {item['clipped']} px"
        assert item["lines"] == 1, f"подпись «{item['label']}» на {item['lines']} строк"
        assert item["height"] >= 44 - 0.5, f"цель «{item['label']}» {item['height']} px"

    widths = [round(item["width"]) for item in state["items"]]
    for got, want, item in zip(widths, expected, state["items"], strict=True):
        assert abs(got - want) <= 1, (
            f"ширина «{item['label']}» {got} px, ожидалось {want}±1: {widths}")
    assert abs(sum(widths) - state["inner"]) <= 1, (
        f"сегменты заняли {sum(widths)} px при ширине {state['inner']} px")

    assert state["overflow"] <= 0, f"экран переполнен по горизонтали на {state['overflow']} px"
    assert state["card"]["left"] >= 0, state["card"]
    assert state["card"]["right"] <= state["viewport"], state["card"]


def test_три_сегмента_на_320_без_обрезки_и_переноса(app_page) -> None:
    """AC-9 и AC-10: на 320 px три подписи читаются целиком, ширины 70/110/74.

    Проверяются оба состояния переключателя, потому что ширину сегмента двигает
    жирность выбранного (`.ui-segmented__item--on`, `font-weight: 600`): подпись
    выбранного шире на 2,5 px, и `flex` отдаёт ей больше места. Замер §9.12
    (70 / 110 / 74) снят в состоянии по умолчанию — там выбран «Труппа»; когда
    выбран «Педагог» (самый тесный набор — ещё и с подсказкой), выходит
    67 / 110 / 77 при той же сумме 254 px. Допуск ±1 px: округление `flex`
    съедает пиксель, а в WebKit метрики шрифта другие.
    """
    page = _open(app_page)
    page.set_viewport_size({"width": 320, "height": 658})
    page.wait_for_timeout(300)

    _fits(page.evaluate(MEASURE), [70, 110, 74])          # умолчание: выбран «Труппа»

    page.locator(f'{SEGMENTS}[data-role="master"]').click()
    page.wait_for_selector("#invite-hint:not([hidden])", timeout=5_000)
    page.wait_for_timeout(300)

    _fits(page.evaluate(MEASURE), [67, 110, 77])          # выбран «Педагог»
    assert page.svoi_errors == [], page.svoi_errors
