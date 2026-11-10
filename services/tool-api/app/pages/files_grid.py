"""Плитка файла: иконка вида, название в три строки, «Удалить» соседом ссылки.

Раздел «Мои файлы» был плоским списком `.ui-row`: имя в 67 знаков не переносилось и
уходило за рамку, а горизонтального скролла нет (`body.ui-page` — `overflow-x: clip`),
поэтому имя было не дочитать. Плитка решает это раскладкой, а не обрезкой по длине:
сетка в две колонки, длинное имя получает всю строку (`--wide`, решение по
`scrollHeight > clientHeight`, docs/development/155 §2.2).

Модуль держит две вещи:

* `ICON_FOR` — выбор символа спрайта (`iconFor`): расширение решает раньше `kind`,
  потому что pdf приходит как `kind='text'`, а слово вида и иконка обязаны отвечать
  одинаково (D8, docs/development/150 §4);
* `SCRIPT` — `iconSvg`, `fileTile`, `fitTiles`.

Скрипт выполняется в том же замыкании, что `files.py` и `files_tree.py`: помощники
(`$`, `el`) приходят из CORE, а `humanSize`/`mmss`/`kindWord` — из соседних блоков
страницы. Поэтому здесь только объявления функций: ни одного обработчика на верхнем
уровне.

Обоснование: docs/development/155-miniapp-files-grid-and-tree.md §2.2, §3 D3,
docs/development/91-design-system.md §4.11, §4.13.
"""

from __future__ import annotations

# Вид файла → символ спрайта. Возвращается имя без «#»: ссылка собирается в `iconSvg`.
ICON_FOR = r"""
  function iconFor(kind, name) {
    var ext = String(name || "").split(".").pop().toLowerCase();
    if (["pdf", "docx", "xlsx", "pptx", "odt", "ods", "rtf"].indexOf(ext) >= 0) {
      return "ui-i-document";
    }
    if (["txt", "md", "csv"].indexOf(ext) >= 0) return "ui-i-text";
    if (kind === "image") return "ui-i-image";
    if (kind === "audio") return "ui-i-audio";
    if (kind === "document") return "ui-i-document";
    if (kind === "text") return "ui-i-text";
    return "ui-i-file";
  }
"""

SCRIPT = r"""
  // Один рисунок на все экраны: спрайт вставляет каркас (`layout.SPRITE`), здесь —
  // только ссылка на символ. Иконка декоративна: смысл несут название и слово вида.
  function iconSvg(name) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "ui-icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var use = document.createElementNS(ns, "use");
    use.setAttribute("href", "#" + name);
    svg.appendChild(use);
    return svg;
  }

  function fileTile(file) {
    var cell = el("div", "ui-grid__cell");
    // Вся плитка — ссылка: клавиатурный фокус и цель во всю площадь, чего у
    // `.ui-row__main` с обработчиком click не было (155 §3 D3).
    var link = el("a", "ui-grid__tile");
    link.href = "/webapp/file?id=" + file.id;
    var box = el("span", "ui-grid__icon");
    box.setAttribute("aria-hidden", "true");
    box.appendChild(iconSvg(iconFor(file.kind, file.name)));
    link.appendChild(box);

    var body = el("span", "ui-grid__body");
    body.appendChild(el("span", "ui-grid__name", file.name));
    var meta = [humanSize(file.size_bytes), kindWord(file.kind, file.name)];
    if (file.duration_sec) meta.push(mmss(file.duration_sec));
    if (file.folder) meta.push(file.folder);
    body.appendChild(el("span", "ui-grid__meta", meta.join(" \u00b7 ")));
    link.appendChild(body);
    cell.appendChild(link);

    // «Удалить» — сосед ссылки, а не её потомок: вложенная кнопка невалидна и ломает
    // фокус. Открывает прежний лист `askDeleteFile()` (V-045).
    deleteFileButton(file, cell);
    return cell;
  }

  // Широкая плитка — по раскладке, а не по длине имени: обрезано ли имя, знает только
  // браузер (`scrollHeight` против `clientHeight`). Идемпотентно: модификаторы
  // снимаются перед каждым замером, поэтому повторный вызов не копит классы.
  var tilesResizeArmed = false;
  var tilesResizeTimer = null;

  function fitTiles(grid) {
    if (!grid) return;
    var cells = Array.prototype.slice.call(grid.querySelectorAll(".ui-grid__cell"));
    cells.forEach(function (cell) {
      cell.classList.remove("ui-grid__cell--wide");
      var tile = cell.querySelector(".ui-grid__tile");
      if (tile) tile.classList.remove("ui-grid__tile--wide");
    });
    cells.forEach(function (cell) {
      var name = cell.querySelector(".ui-grid__name");
      if (!name) return;
      if (name.scrollHeight - name.clientHeight <= 1) return;
      cell.classList.add("ui-grid__cell--wide");
      var tile = cell.querySelector(".ui-grid__tile");
      if (tile) tile.classList.add("ui-grid__tile--wide");
    });
    if (tilesResizeArmed) return;
    tilesResizeArmed = true;
    // Поворот телефона меняет ширину плитки: пересчитываем после того, как браузер
    // применил раскладку, — 100 мс хватает и не мигает в глазах.
    window.addEventListener("resize", function () {
      if (tilesResizeTimer) clearTimeout(tilesResizeTimer);
      tilesResizeTimer = setTimeout(function () { fitTiles($("list")); }, 100);
    });
  }
"""
