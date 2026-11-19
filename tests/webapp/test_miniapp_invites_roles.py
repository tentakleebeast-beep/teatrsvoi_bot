"""Роль «Педагог» в выпуске инвайт-кода из Mini App: браузерный сценарий.

Обоснование: `docs/development/176` §9.10 (AC-5…AC-10), карта `t_ff6144fc`, эпик E10.
Проверяется то, что видит администратор: три сегмента роли с подписями «Труппа ·
Организатор · Педагог», подсказка о дисциплине только у педагога, выпуск кода
педагогу (тело запроса и лист) и отсутствие переключателя у роли без права.

Отдельно — узкая ширина: на 320 px три подписи должны читаться целиком и не
переноситься. Абсолютные ширины 70 / 110 / 74 из §9.12 — замер chromium на macOS
(`inner` 254 px); гейт идёт на ubuntu-latest, где шрифт шире, и там те же три
подписи просят 78 / 119 / 81 = 277 px при том же `inner` 254 px, то есть ряд
шире контейнера на 23 px, но без обрезки и переноса (CI run 44443080566). Поэтому
проверяются инварианты раскладки, а не платформенное число: подпись не сжата ниже
своей натуральной ширины, ряд занимает max(содержимое, сумма подписей),
«Организатор» шире обоих коротких, короткие делят остаток.

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

#: Замер сегмента: ширина, высота цели, обрезка, число строк подписи и её
#: натуральная ширина в том же шрифте (без сжатия flex'ом).
MEASURE = """
() => {
  const box = document.getElementById('invite-role');
  const style = window.getComputedStyle(box);
  // Сколько подписи нужно при её собственном шрифте: пробный span вне раскладки.
  // Так проверка не зависит от метрик платформы — сравниваются два замера страницы.
  const natural = (node) => {
    const cs = window.getComputedStyle(node);
    const probe = document.createElement('span');
    probe.textContent = node.textContent.trim();
    probe.style.cssText =
      'position:absolute;top:-9999px;left:0;white-space:nowrap;visibility:hidden';
    probe.style.fontFamily = cs.fontFamily;
    probe.style.fontSize = cs.fontSize;
    probe.style.fontWeight = cs.fontWeight;
    probe.style.fontStyle = cs.fontStyle;
    probe.style.fontStretch = cs.fontStretch;
    probe.style.letterSpacing = cs.letterSpacing;
    probe.style.textTransform = cs.textTransform;
    document.body.appendChild(probe);
    const text = probe.getBoundingClientRect().width;
    probe.remove();
    return text + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  };
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
      intrinsic: natural(node),
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


def _fits(state: dict, where: str) -> None:
    """Инварианты раскладки трёх сегментов, не зависящие от метрик шрифта.

    Ни одна проверка не называет абсолютных пикселей: и ожидание, и факт берутся
    из самого замера, поэтому тест одинаково верен для macOS-шрифта (254 px
    содержимого, ряд в контейнере) и для ubuntu-шрифта в CI (тот же `inner`
    254 px, подписи просят 277 px).
    """
    assert len(state["items"]) == 3, state
    by_role = {item["role"]: item for item in state["items"]}
    assert set(by_role) == {"troupe", "org", "master"}, by_role

    # AC-9: подпись читается целиком — без обрезки, в одну строку, цель ≥ 44 px.
    for item in state["items"]:
        assert item["clipped"] <= 1, (
            f"{where}: подпись «{item['label']}» обрезана на {item['clipped']} px")
        assert item["lines"] == 1, (
            f"{where}: подпись «{item['label']}» на {item['lines']} строк")
        assert item["height"] >= 44 - 0.5, (
            f"{where}: цель «{item['label']}» {item['height']} px")

    # Ни один сегмент не сжат flex'ом ниже своей подписи: ширина сегмента не
    # меньше натуральной ширины подписи с полями. 2 px запаса — округление долей.
    for item in state["items"]:
        assert item["width"] >= item["intrinsic"] - 2, (
            f"{where}: сегмент «{item['label']}» сжат до {item['width']:.1f} px "
            f"при ширине подписи {item['intrinsic']:.1f} px")

    widths = {role: item["width"] for role, item in by_role.items()}
    inner = state["inner"]
    natural = sum(item["intrinsic"] for item in state["items"])
    occupied = sum(widths.values())
    # Сегменты занимают либо всю ширину содержимого контейнера (подписи уже своих
    # долей — flex раздаёт остаток), либо ровно столько, сколько требуют подписи:
    # подпись не сжимается, и при широком шрифте ряд выходит за контейнер, который
    # его подрезает (`overflow: hidden`). Так и происходит в CI, где шрифт шире
    # системного телефонного: три подписи просят 277 px при 254 px содержимого.
    assert abs(occupied - max(inner, natural)) <= 2, (
        f"{where}: сегменты заняли {occupied:.1f} px при содержимом "
        f"{inner} px и подписях {natural:.1f} px")

    # Длинная подпись «Организатор» не уступает коротким: сегмент шире обоих.
    # Треть как точка отсчёта не годится — при широком шрифте короткая подпись
    # сама просит больше трети (Verdana: «Педагог» 85 px против 84.7), и равенство
    # долей перестаёт быть свойством раскладки.
    org = by_role["org"]
    assert org["width"] > org["intrinsic"] - 2, (
        f"{where}: «Организатор» {org['width']:.1f} px сжат при подписи "
        f"{org['intrinsic']:.1f} px")
    for role in ("troupe", "master"):
        assert by_role[role]["width"] < widths["org"], (
            f"{where}: «{by_role[role]['label']}» {widths[role]:.1f} px не уже "
            f"«Организатора» {widths['org']:.1f} px")

    # Короткие делят остаток: их ширины расходятся не больше, чем расходятся их
    # подписи, плюс 2 px на округление долей. Равенство «±1 px» из AC-10 —
    # свойство macOS-шрифта; на ubuntu разница подписей даёт 3 px (78 и 81).
    short = [by_role[role] for role in ("troupe", "master")]
    spread = abs(short[0]["width"] - short[1]["width"])
    labels = abs(short[0]["intrinsic"] - short[1]["intrinsic"])
    assert spread <= labels + 2, (
        f"{where}: ширины «{short[0]['label']}» и «{short[1]['label']}» "
        f"разошлись на {spread:.1f} px при разнице подписей {labels:.1f} px")

    assert state["overflow"] <= 0, (
        f"{where}: экран переполнен по горизонтали на {state['overflow']} px")
    assert state["card"]["left"] >= 0, state["card"]
    assert state["card"]["right"] <= state["viewport"], state["card"]


def test_три_сегмента_на_320_без_обрезки_и_переноса(app_page) -> None:
    """AC-9 и AC-10: на 320 px три подписи читаются целиком, раскладка цела.

    Абсолютные ширины AC-10 (70 / 110 / 74) — замер chromium на macOS с `inner`
    254 px; в CI (ubuntu-latest, тот же `inner` 254 px, но шрифт шире) тот же
    экран даёт 78 / 119 / 81 = 277 px, и ряд выходит за контейнер.
    Число — свойство окружения, поэтому проверяются инварианты: подпись не сжата
    ниже своей натуральной ширины, ряд занимает max(содержимое, сумма подписей),
    «Организатор» шире обоих коротких, «Труппа» и «Педагог» делят остаток.

    Проверяются оба состояния переключателя, потому что ширину сегмента двигает
    жирность выбранного (`.ui-segmented__item--on`, `font-weight: 600`).
    """
    page = _open(app_page)
    page.set_viewport_size({"width": 320, "height": 658})
    page.wait_for_timeout(300)

    _fits(page.evaluate(MEASURE), "выбран «Труппа»")      # состояние по умолчанию

    page.locator(f'{SEGMENTS}[data-role="master"]').click()
    page.wait_for_selector("#invite-hint:not([hidden])", timeout=5_000)
    page.wait_for_timeout(300)

    _fits(page.evaluate(MEASURE), "выбран «Педагог»")     # самый тесный набор
    assert page.svoi_errors == [], page.svoi_errors
