"""Дерево папок: иерархия собирается на клиенте из плоского ответа `/webapp/folders`.

Сервер отдаёт строки `order by path` («Спектакли», «Спектакли/Бунин»,
«Спектакли/Бунин/заметки» подряд), поэтому вложенность в списке не видна, а три кнопки
строки («Открыть», «Имя», «Закрыть») оставляли названию 70 px на 390 и 0 px на 320.
API не меняется: `parent_id`, `path`, `depth` уже в ответе; иерархию собирает клиент
(docs/development/155 §4).

Модуль держит три вещи:

* `TREE_BUILD` — чистая `buildFolderTree(rows)`, отдельной константой: её гоняет юнит-тест
  в node без DOM (`tests/unit/test_webapp_files_tree.py`);
* `SCRIPT` — отрисовка (`folderNode`, `renderFolders`, `loadFolders`), лист действий над
  папкой (`folderMenu`, `askRenameFolder`, `askCloseFolder`) и состояние раскрытия;
* правила, которые нельзя потерять при правке: `aria-expanded` стоит на кнопке
  (disclosure, не `role=tree`), `aria-controls` ссылается на свой `<ul>`, дерево
  раскрыто по умолчанию, свёрнутые папки помнятся на сеанс в `sessionStorage`.

`depth` из ответа сервера **не используется**: после выноса сироты или узла цикла наверх
сохранённая глубина дала бы отступ без видимого родителя. `level` считается по факту.

Скрипт выполняется в том же замыкании, что `files.py` и `files_grid.py`: `$`, `el`,
`empty`, `sheet`, `toast`, `problem` приходят из CORE, `iconSvg` — из `files_grid`.

Обоснование: docs/development/155-miniapp-files-grid-and-tree.md §4, §5.2, §7.2–7.3,
docs/development/91-design-system.md §4.12.
"""

from __future__ import annotations

# Чистая сборка дерева. Порядок шагов важен и описан в 155 §4: карта → родитель →
# циклы → вложение → уровень → порядок. DOM не трогает, в хранилище не пишет.
TREE_BUILD = r"""
  function folderId(value) {
    if (typeof value === "number" && isFinite(value) && value % 1 === 0) return value;
    if (typeof value === "string" && /^\d+$/.test(value)) return Number(value);
    return null;
  }

  function buildFolderTree(rows) {
    var map = {};
    var flat = [];
    (rows || []).forEach(function (row) {
      if (!row) return;
      var id = folderId(row.id);
      // Строку без числового id отбрасываем: у неё нет адреса, вести её некуда.
      if (id === null || map.hasOwnProperty(id)) return;
      var node = { id: id, name: String(row.name || ""), path: String(row.path || ""),
                   parentId: null, orphan: false, level: 1, children: [] };
      map[id] = node;
      flat.push(node);
      node.parentId = folderId(row.parent_id);
    });

    // Родитель, которого нет в ответе (папка закрыта или удалена), — сирота:
    // поднимаем наверх и показываем полный путь, а не отступ в пустоту.
    flat.forEach(function (node) {
      var parentId = node.parentId;
      if (parentId === null || parentId === node.id || !map.hasOwnProperty(parentId)) {
        node.parentId = null;
        node.orphan = parentId !== null;
      }
    });

    // Цикл: берём недостижимый узел с наименьшим id, делаем его корнем и пересчитываем
    // достижимость. Каждый проход добавляет ровно один корень, поэтому обход конечен,
    // а каждый узел цикла виден ровно один раз (a↔b → a корень, b его ребёнок).
    function reachable() {
      var seen = {};
      var stack = flat.filter(function (node) { return node.parentId === null; });
      while (stack.length) {
        var node = stack.pop();
        if (seen[node.id]) continue;
        seen[node.id] = true;
        flat.forEach(function (child) {
          if (child.parentId === node.id) stack.push(child);
        });
      }
      return seen;
    }

    var seen = reachable();
    var lost = flat.filter(function (node) { return !seen[node.id]; });
    while (lost.length) {
      var lowest = lost[0];
      lost.forEach(function (node) { if (node.id < lowest.id) lowest = node; });
      lowest.parentId = null;
      // Путь у узла цикла настоящий, но показать его полезно: иначе строка наверху
      // выглядит корнем, которым не является.
      lowest.orphan = true;
      seen = reachable();
      lost = flat.filter(function (node) { return !seen[node.id]; });
    }

    var roots = [];
    flat.forEach(function (node) {
      if (node.parentId === null) roots.push(node);
      else map[node.parentId].children.push(node);
    });

    // Порядок — по названию внутри каждого уровня: `numeric` ставит «01 Увертюра»
    // перед «10 Финал», `sensitivity: "base"` не даёт регистру влиять.
    function byName(a, b) {
      return a.name.localeCompare(b.name, "ru", { numeric: true, sensitivity: "base" });
    }

    function place(nodes, level) {
      nodes.sort(byName);
      nodes.forEach(function (node) {
        node.level = level;
        place(node.children, level + 1);
      });
    }

    place(roots, 1);

    function plain(node) {
      return { id: node.id, name: node.name, path: node.path, level: node.level,
               orphan: node.orphan, children: node.children.map(plain) };
    }

    return roots.map(plain);
  }
"""

SCRIPT = r"""
  // --- дерево папок: раскрытие, лист действий (155 §5.2, §7.2–7.3) -------------

  // Помним именно свёрнутые папки: новая папка тогда появляется раскрытой, как все,
  // а не «выключенной» по умолчанию. Приватный режим бросает на `sessionStorage` —
  // тогда дерево просто раскрыто целиком.
  var COLLAPSE_KEY = "svoi.files.collapsed.v1";
  var collapsedFolders = [];

  function readCollapsed() {
    collapsedFolders = [];
    try {
      var raw = window.sessionStorage.getItem(COLLAPSE_KEY);
      var list = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(list)) return;
      list.forEach(function (id) {
        var value = folderId(id);
        if (value !== null) collapsedFolders.push(value);
      });
    } catch (error) {
      collapsedFolders = [];
    }
  }

  function saveCollapsed() {
    try {
      window.sessionStorage.setItem(COLLAPSE_KEY, JSON.stringify(collapsedFolders));
    } catch (error) {
      // Без хранилища дерево работает, просто не помнит свёрнутое.
    }
  }

  function isCollapsed(id) { return collapsedFolders.indexOf(Number(id)) >= 0; }

  function folderNode(node) {
    var li = el("li", "ui-tree__node");
    var cls = "ui-tree__row";
    // Отступ 16 px на уровень, но не глубже третьего: дальше он съедает название.
    if (node.level >= 3) cls += " ui-tree__row--l3";
    else if (node.level === 2) cls += " ui-tree__row--l2";
    var row = el("div", cls);
    var kidsId = "folder-kids-" + node.id;
    var collapsed = isCollapsed(node.id);
    var hasKids = node.children && node.children.length;

    var toggle = el("button", "ui-tree__toggle");
    toggle.type = "button";
    if (hasKids) {
      // Раскрытие — паттерн disclosure: `aria-expanded` на кнопке плюс вложенный
      // список. Полная модель `role=tree` требует стрелок и roving tabindex, а
      // половина её без клавиатурных тестов — обещание без проверки.
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      toggle.setAttribute("aria-controls", kidsId);
      toggle.setAttribute("aria-label",
        (collapsed ? "Раскрыть папку " : "Свернуть папку ") + node.name);
      toggle.appendChild(el("span", "ui-tree__chevron", "\u203a"));
    } else {
      // Слот переключателя остаётся у листа, иначе названия встают в разные столбцы.
      toggle.disabled = true;
      toggle.setAttribute("aria-hidden", "true");
      toggle.tabIndex = -1;
    }

    var body = el("a", "ui-tree__body");
    body.href = "/webapp/folder?id=" + node.id;
    body.appendChild(el("span", "ui-tree__name", node.name));
    if (node.orphan && node.path) body.appendChild(el("span", "ui-tree__meta", node.path));

    var more = el("button", "ui-tree__more");
    more.type = "button";
    more.setAttribute("aria-label", "Действия с папкой " + node.name);
    more.appendChild(iconSvg("ui-i-more"));
    more.addEventListener("click", function () { folderMenu(node); });

    row.appendChild(toggle);
    row.appendChild(body);
    row.appendChild(more);
    li.appendChild(row);

    var kids = null;
    if (hasKids) {
      kids = el("ul", "ui-tree__children");
      kids.id = kidsId;
      kids.hidden = collapsed;
      node.children.forEach(function (child) { kids.appendChild(folderNode(child)); });
      li.appendChild(kids);
      toggle.addEventListener("click", function () {
        var nowCollapsed = !isCollapsed(node.id);
        if (nowCollapsed) collapsedFolders.push(Number(node.id));
        else collapsedFolders = collapsedFolders.filter(function (id) {
          return Number(id) !== Number(node.id);
        });
        saveCollapsed();
        toggle.setAttribute("aria-expanded", nowCollapsed ? "false" : "true");
        toggle.setAttribute("aria-label",
          (nowCollapsed ? "Раскрыть папку " : "Свернуть папку ") + node.name);
        if (kids) kids.hidden = nowCollapsed;
      });
    }

    return li;
  }

  // Пустое состояние внутри `<ul>`: `<div>` в списке — невалидная разметка, поэтому
  // `.ui-empty` живёт в `<li>`.
  function folderState(title, text) {
    var li = el("li", "ui-tree__empty");
    li.appendChild(empty(title, text));
    return li;
  }

  function countNodes(nodes) {
    return (nodes || []).reduce(function (sum, node) {
      return sum + 1 + countNodes(node.children);
    }, 0);
  }

  function renderFolders(rows) {
    var list = $("folders");
    clear(list);
    var nodes = buildFolderTree(rows || []);
    var total = countNodes(nodes);
    $("folder-count").textContent = total
      ? total + " " + plural(total, "папка", "папки", "папок") : "";
    if (!nodes.length) {
      list.appendChild(folderState("Папок пока нет",
        "Создайте первую — общую папку видят все, но правит только администратор."));
      return;
    }
    nodes.forEach(function (node) { list.appendChild(folderNode(node)); });
  }

  function loadFolders() {
    return api("/webapp/folders").then(function (res) {
      if (res.status === 403) { renderFolders([]); return []; }
      if (res.status !== 200) {
        // Своей кнопки «Повторить» нет: плашка `networkGuard` в оболочке уже
        // показывает её и на обрыве, и на 5xx. Экран говорит только, чего нет.
        clear($("folders"));
        $("folder-count").textContent = "";
        $("folders").appendChild(folderState("Папки не загрузились",
          res.status === 0 || res.status >= 500
            ? "Сервер не ответил. Повторите, когда появится связь."
            : problem(res)));
        return [];
      }
      readCollapsed();
      renderFolders(res.body.data || []);
      return res.body.data || [];
    });
  }

  function folderMenu(folder) {
    var body = el("div", "files-confirm");
    var box = el("div", "ui-field");
    var label = el("label", "ui-field__label", "Название");
    label.setAttribute("for", "folder-rename");
    var input = el("input", "ui-input");
    input.id = "folder-rename";
    input.type = "text";
    input.maxLength = 100;
    input.value = folder.name;
    box.appendChild(label);
    box.appendChild(input);
    body.appendChild(box);
    // `keepOpen`: «Переименовать» сначала читает поле, «Закрыть папку» открывает
    // второй вопрос. Лист закрывает сам код после успеха (`closeSheet()`).
    sheet("Папка «" + folder.name + "»", body, [
      { label: "Переименовать", primary: true, keepOpen: true,
        onTap: function () { askRenameFolder(folder); } },
      { label: "Закрыть папку", danger: true, keepOpen: true,
        onTap: function () { askCloseFolder(folder); } },
      { label: "Отмена" }
    ]);
  }

  function askRenameFolder(folder) {
    var input = $("folder-rename");
    var name = input ? String(input.value || "").trim() : "";
    if (!name) { toast("Название не может быть пустым."); return; }
    api("/webapp/folders/" + folder.id + "/rename", { name: name })
      .then(function (res) {
        if (res.status !== 200) { toast(problem(res)); return; }
        closeSheet();
        folderNote("Папка переименована: " + res.body.path + ".", false);
        loadFolders();
      });
  }

  function askCloseFolder(folder) {
    var body = el("div", "files-confirm");
    body.appendChild(el("p", "ui-text",
      "Папка закроется у всех. Файлы останутся — они в «Без папки»."));
    // «Закрыть» внутри вопроса — отдельный `closeFolder()` из `files_actions.py`:
    // маршрут и текст последствия одни на экран и чат.
    sheet("Закрыть папку «" + folder.name + "»?", body, [
      { label: "Отмена" },
      { label: "Закрыть", danger: true, onTap: function () { closeFolder(folder); } }
    ]);
  }
"""
