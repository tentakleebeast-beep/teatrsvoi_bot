"""Сетка плиток и дерево папок живьём: 390 и 320 px, Chromium (В2/В3).

Раздел «Мои файлы» был плоским списком: имя в 67 знаков не переносилось и уходило за
рамку, а горизонтального скролла нет (`body.ui-page` — `overflow-x: clip`), то есть имя
было не дочитать. Папки шли в серверном порядке `order by path`, вложенность не видна,
а три кнопки строки оставляли названию 0 px на 320.

Здесь проверяется поведение, а не строки кода: сетка в две колонки, ни одно из девяти
имён стенда не обрезано, широких плиток хотя бы одна, нет горизонтальной прокрутки,
иконка отвечает виду, «Удалить» 44×44 не пересекает название; дерево раскрыто по
умолчанию, переключатель меняет число видимых строк, состояние переживает перерисовку,
сирота наверху с полным путём, уровни дают отступ 8/24/40.

    SVOI_WEBAPP_TESTS=1 env -u PYTHONPATH .venv/bin/python -m pytest tests/webapp -q --browser chromium

Обоснование: docs/development/155-miniapp-files-grid-and-tree.md §2.2, §2.3, §5.1–5.2,
§7.1–7.2, критерии приёмки 1–5; карта t_9297d656.
"""

from __future__ import annotations

import pytest

WIDTHS = {
    "390": {"width": 390, "height": 844, "cell": 173},
    "360": {"width": 360, "height": 800, "cell": 158},
    "320": {"width": 320, "height": 693, "cell": 138},
}


def _tiles(page) -> None:
    page.wait_for_function(
        "() => document.querySelectorAll('#list .ui-grid__cell').length === 9", timeout=10_000)


def _folders(page) -> None:
    # Последняя строка дерева — «Без папки · N» (строка-фильтр, V-065), а не папка:
    # считаем только узлы-папки, иначе ожидание «шесть» ловит семь.
    page.wait_for_function(
        "() => document.querySelectorAll('#folders .ui-tree__node:not(.ui-tree__node--nofolder)')"
        ".length === 6", timeout=10_000)


def _size(page, width: dict) -> None:
    """Переставить окно на ширину профиля и дать `fitTiles` пересчитать плитки.

    Ждём не «примерно 300 мс», а факт пересчёта: после поворота/смены ширины
    `fitTiles` срабатывает по debounce 100 мс, и под нагрузкой полного прогона
    один кадр мог ещё не переставить широкую плитку (падение 360 px в гейте).
    Условие — ни одного обрезанного имени; если `fitTiles` не сработал, будет
    таймаут, а не ложный зелёный.
    """
    page.set_viewport_size({"width": width["width"], "height": width["height"]})
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('#list .ui-grid__name'))"
        ".every(function (node) { return node.scrollHeight - node.clientHeight <= 1; })",
        timeout=5_000)


def _visible_rows(page) -> int:
    """Видимые строки папок; строка-фильтр «Без папки» в счёт не идёт (V-065)."""
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('#folders .ui-tree__node'))"
        ".filter(function (node) { return !node.classList.contains('ui-tree__node--nofolder'); })"
        ".map(function (node) { return node.querySelector('.ui-tree__row'); })"
        ".filter(function (row) { return row && row.offsetParent !== null; }).length")


@pytest.mark.parametrize("name", list(WIDTHS))
def test_сетка_в_две_колонки(app_page, name) -> None:
    """Критерий 1: `#list` — сетка, две колонки, клетка 173 / 138 px (допуск ±3)."""
    width = WIDTHS[name]
    page = app_page("/webapp/files")
    _tiles(page)
    _size(page, width)

    grid = page.evaluate("""() => {
      var g = document.getElementById('list');
      var st = getComputedStyle(g);
      var first = g.querySelector('.ui-grid__cell').getBoundingClientRect();
      return {display: st.display, columns: st.gridTemplateColumns, cell: first.width};
    }""")
    assert grid["display"] == "grid", grid
    assert len(grid["columns"].split()) == 2, f"колонок не две: {grid['columns']}"
    assert abs(grid["cell"] - width["cell"]) <= 3, grid
    assert not page.svoi_errors, page.svoi_errors


@pytest.mark.parametrize("name", list(WIDTHS))
def test_длинные_имена_не_обрезаны(app_page, name) -> None:
    """Критерий 1: ни одно из девяти имён стенда не обрезано, широких плиток ≥ 1."""
    width = WIDTHS[name]
    page = app_page("/webapp/files")
    _tiles(page)
    _size(page, width)

    cut = page.evaluate("""() => Array.from(document.querySelectorAll('#list .ui-grid__name'))
      .map(function (node) { return {text: node.textContent,
                                     cut: node.scrollHeight - node.clientHeight}; })
      .filter(function (item) { return item.cut > 1; })""")
    assert not cut, f"имена обрезаны на {width['width']} px: {[item['text'][:30] for item in cut]}"

    wide = page.locator("#list .ui-grid__cell--wide").count()
    assert wide >= 1, "ни одна плитка не стала широкой"
    assert page.locator("#list .ui-grid__cell--wide .ui-grid__name").count() == wide
    assert not page.svoi_errors, page.svoi_errors


@pytest.mark.parametrize("name", list(WIDTHS))
def test_нет_горизонтального_скролла_на_файлах(app_page, name) -> None:
    """Критерий 1: документ не шире окна и ни один контейнер не прокручивается вбок."""
    width = WIDTHS[name]
    page = app_page("/webapp/files")
    _tiles(page)
    _size(page, width)

    metrics = page.evaluate("""() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
      inner: window.innerWidth,
    })""")
    assert metrics["scroll"] - metrics["client"] <= 1, metrics
    assert metrics["client"] <= metrics["inner"] + 1, metrics

    sideways = page.evaluate("""() => Array.from(document.querySelectorAll('body *'))
      .filter(function (node) {
        var st = getComputedStyle(node);
        var hidden = st.overflowX === 'hidden' || st.overflowX === 'clip';
        return !hidden && node.scrollWidth - node.clientWidth > 1;
      }).map(function (node) { return node.className || node.tagName; }).slice(0, 5)""")
    assert not sideways, f"контейнеры с прокруткой вбок: {sideways}"
    assert not page.svoi_errors, page.svoi_errors


def test_иконка_по_виду(app_page) -> None:
    """Критерий 8: аудио, документ и картинка — разные символы, и каждый есть в спрайте."""
    page = app_page("/webapp/files")
    _tiles(page)

    for name, symbol in (("Репетиция 12 октября", "ui-i-audio"),
                         ("Гаммы для хора", "ui-i-document"),
                         ("Афиша_осеннего_сезона", "ui-i-image")):
        cell = page.locator("#list .ui-grid__cell").filter(has_text=name).first
        use = cell.locator(".ui-grid__icon use").get_attribute("href")
        assert use == "#" + symbol, f"{name}: иконка {use}"
        assert page.evaluate("(id) => !!document.getElementById(id)", symbol), \
            f"символ {symbol} не нарисован в спрайте"

    # Иконка декоративна: смысл несут название и слово вида.
    assert page.locator("#list .ui-grid__icon[aria-hidden='true']").count() == 9
    assert "документ" in page.locator("#list .ui-grid__cell").filter(
        has_text="Гаммы для хора").first.inner_text()
    assert not page.svoi_errors, page.svoi_errors


def test_удаление_сорок_четыре(app_page) -> None:
    """Критерий 2: «Удалить» 44×44 и не пересекает название — иначе его не прочитать."""
    page = app_page("/webapp/files")
    _tiles(page)

    boxes = page.evaluate("""() => Array.from(document.querySelectorAll('#list .ui-grid__cell'))
      .map(function (cell) {
        var button = cell.querySelector('.ui-grid__remove').getBoundingClientRect();
        var name = cell.querySelector('.ui-grid__name').getBoundingClientRect();
        var cross = !(button.right <= name.left || button.left >= name.right ||
                      button.bottom <= name.top || button.top >= name.bottom);
        return {w: button.width, h: button.height, cross: cross,
                label: cell.querySelector('.ui-grid__remove').getAttribute('aria-label')};
      })""")
    for box in boxes:
        assert abs(box["w"] - 44) <= 1 and abs(box["h"] - 44) <= 1, box
        assert not box["cross"], f"кнопка удаления лежит на названии: {box['label']}"
        assert box["label"].startswith("Удалить файл: "), box
    assert not page.svoi_errors, page.svoi_errors


def test_дерево_раскрыто_по_умолчанию(app_page) -> None:
    """Критерий 6: шесть папок, все раскрыты — свёрнутое прятало бы имена."""
    page = app_page("/webapp/files")
    _folders(page)

    assert page.locator("#folders .ui-tree__node:not(.ui-tree__node--nofolder)").count() == 6
    states = page.locator("#folders .ui-tree__toggle[aria-expanded]").evaluate_all(
        "nodes => nodes.map(node => node.getAttribute('aria-expanded'))")
    assert states and set(states) == {"true"}, states
    assert _visible_rows(page) == 6
    assert not page.svoi_errors, page.svoi_errors


def test_переключатель_меняет_число_видимых_строк(app_page) -> None:
    """Критерий 5: свернул «Спектакли» — видимых строк 3, подпись сменилась."""
    page = app_page("/webapp/files")
    _folders(page)

    row = page.locator("#folders .ui-tree__row").filter(has_text="Спектакли").first
    toggle = row.locator(".ui-tree__toggle")
    assert toggle.get_attribute("aria-label") == "Свернуть папку Спектакли"
    kids_id = page.evaluate("""() => {
      var row = Array.from(document.querySelectorAll('#folders .ui-tree__row'))
        .filter(function (item) {
          return item.querySelector('.ui-tree__name').textContent === 'Спектакли';
        })[0];
      return row.querySelector('.ui-tree__toggle').getAttribute('aria-controls');
    }""")
    assert kids_id and page.locator("#" + kids_id).count() == 1, "aria-controls в никуда"
    assert toggle.get_attribute("aria-controls") == kids_id, "переключатель не связан со списком"

    toggle.click()
    page.wait_for_function("(id) => document.getElementById(id).hidden === true",
                           arg=kids_id, timeout=5_000)

    assert toggle.get_attribute("aria-expanded") == "false"
    assert toggle.get_attribute("aria-label") == "Раскрыть папку Спектакли"
    assert _visible_rows(page) == 3
    assert page.locator("#folders .ui-tree__node:not(.ui-tree__node--nofolder)").count() == 6, \
        "свёрнутое удалило строки"
    assert not page.svoi_errors, page.svoi_errors


def test_состояние_переживает_перерисовку(app_page) -> None:
    """Критерий 6: свёрнутое помнится на сеанс и применяется при каждой отрисовке."""
    page = app_page("/webapp/files")
    _folders(page)

    page.locator("#folders .ui-tree__row").filter(
        has_text="Спектакли").first.locator(".ui-tree__toggle").click()
    page.wait_for_function(
        "() => sessionStorage.getItem('svoi.files.collapsed.v1') !== null", timeout=5_000)

    # Перерисовка дерева через действие экрана: создание папки зовёт `loadFolders()`.
    page.fill("#folder-name", "Реквизит")
    page.click("#folder-create")
    page.wait_for_function(
        "() => document.querySelectorAll"
        "('#folders .ui-tree__node:not(.ui-tree__node--nofolder)').length === 6", timeout=10_000)
    page.wait_for_timeout(300)

    toggle = page.locator("#folders .ui-tree__row").filter(
        has_text="Спектакли").first.locator(".ui-tree__toggle")
    assert toggle.get_attribute("aria-expanded") == "false", "состояние сбросилось при перерисовке"
    assert _visible_rows(page) == 3
    assert not page.svoi_errors, page.svoi_errors


def test_сирота_наверху_с_путём(app_page) -> None:
    """Критерий 3: ребёнок закрытой папки — на верхнем уровне с полным путём."""
    page = app_page("/webapp/files")
    _folders(page)

    orphan = page.evaluate("""() => {
      var rows = Array.from(document.querySelectorAll('#folders .ui-tree__row'));
      var row = rows.filter(function (item) {
        return item.querySelector('.ui-tree__name').textContent === 'старое';
      })[0];
      if (!row) return null;
      var meta = row.querySelector('.ui-tree__meta');
      return {top: row.parentElement.parentElement.id === 'folders',
              path: meta ? meta.textContent : '',
              level: row.className};
    }""")
    assert orphan, "сирота «старое» не нарисована"
    assert orphan["top"], "сирота не на верхнем уровне"
    assert orphan["path"] == "Архив 2024/старое", orphan
    assert "--l2" not in orphan["level"], "сироте оставили отступ без видимого родителя"
    assert not page.svoi_errors, page.svoi_errors


def test_без_папки_фильтрует_сетку(app_page) -> None:
    """Критерий V-065: последняя строка дерева «Без папки · N» фильтрует сетку.

    N — из уже загруженного списка (на стенде три файла без папки), нового запроса
    строка не делает: сеть до и после нажатия одинакова. Повторное нажатие снимает
    фильтр, `aria-pressed` и пояснение говорят состояние словами.
    """
    page = app_page("/webapp/files")
    _tiles(page)
    _folders(page)

    # Последняя строка дерева — «Без папки · N», после всех папок.
    last = page.locator("#folders .ui-tree__row").last
    assert last.inner_text().startswith("Без папки · 3"), last.inner_text()
    assert page.locator("#folders .ui-tree__node--nofolder").count() == 1
    assert last.get_attribute("aria-pressed") == "false"
    assert page.locator("#list .ui-grid__cell").count() == 9

    # Ни одного запроса за списком: фильтр работает на уже загруженном (V-065).
    calls: list[str] = []
    page.on("request", lambda request: calls.append(request.url))
    last.click()
    page.wait_for_function(
        "() => document.querySelectorAll('#list .ui-grid__cell').length === 3", timeout=5_000)

    names = page.evaluate(
        "() => Array.from(document.querySelectorAll('#list .ui-grid__name'))"
        ".map(function (node) { return node.textContent; })")
    assert set(names) == {"Репетиция 12 октября.m4a", "Гаммы для хора.pdf",
                          "Афиша_осеннего_сезона_вариант_с_текстом_без_логотипа.png"}, names
    assert page.locator("#no-folder-row").get_attribute("aria-pressed") == "true"
    assert page.locator("#list-note").is_visible(), "пояснение к фильтру не показано"
    assert "без папки" in page.inner_text("#list-note").lower()
    assert page.inner_text("#count") == "3 файла", page.inner_text("#count")

    assert not [url for url in calls if "/files" in url or "/folders" in url], \
        f"переключение фильтра сходило в сеть: {calls}"

    # Второе нажатие — снова все файлы, пояснение убрано.
    page.locator("#no-folder-row").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#list .ui-grid__cell').length === 9", timeout=5_000)
    assert page.locator("#no-folder-row").get_attribute("aria-pressed") == "false"
    assert not page.locator("#list-note").is_visible()
    assert not page.svoi_errors, page.svoi_errors


def test_без_папки_не_ломает_имя_на_320(app_page) -> None:
    """Строка «Без папки» встаёт в столбец названий и не даёт прокрутки вбок на 320."""
    page = app_page("/webapp/files")
    _tiles(page)
    _folders(page)
    page.set_viewport_size({"width": 320, "height": 693})

    geometry = page.evaluate("""() => {
      var row = document.getElementById('no-folder-row');
      var name = row.querySelector('.ui-tree__name');
      var folder = Array.from(document.querySelectorAll('#folders .ui-tree__name'))
        .filter(function (node) { return node.textContent === 'Спектакли'; })[0];
      return {left: name.getBoundingClientRect().left,
              folderLeft: folder.getBoundingClientRect().left,
              scroll: document.documentElement.scrollWidth,
              client: document.documentElement.clientWidth};
    }""")
    assert abs(geometry["left"] - geometry["folderLeft"]) <= 1, \
        f"название «Без папки» не в столбце папок: {geometry}"
    assert geometry["scroll"] - geometry["client"] <= 1, geometry
    assert not page.svoi_errors, page.svoi_errors


def test_уровни_дают_отступ(app_page) -> None:
    """Критерий 3, 91 §4.12: отступ строк 1/2/3 уровня — 8/24/40 px (+0/+16/+32)."""
    page = app_page("/webapp/files")
    _folders(page)

    pads = page.evaluate("""() => {
      var out = {};
      Array.from(document.querySelectorAll('#folders .ui-tree__row')).forEach(function (row) {
        var name = row.querySelector('.ui-tree__name').textContent;
        out[name] = parseFloat(getComputedStyle(row).paddingLeft);
      });
      return out;
    }""")
    assert pads["Спектакли"] == 8, pads
    assert pads["Бунин"] == 24, pads
    assert pads["заметки"] == 40, pads
    assert pads["музыка"] == 40, pads
    assert not page.svoi_errors, page.svoi_errors
