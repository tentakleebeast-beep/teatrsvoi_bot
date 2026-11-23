"""Экран «Файлы»: место, загрузка, поиск и сетка файлов (Ш-08, E4).

Что здесь важнее всего:

* **Кому открыто.** Решение V-039 (в): «Файлы студии» видят admin, org и master (право
  `files.access`, проверка на сервере); без права экран говорит словами (Б3). Кого
  пускает действие, решает домен: `folders` — admin и org, `tags`/`file_access` — admin.
* **Два расположения и поиск (E4).** Файл можно положить в Garage или добавить ссылкой на
  Drive; у файла есть описание и папка. Поиск идёт по имени, папке и типу — чипы над
  сеткой переключают набор, подпись «Найдено: N» говорит, что сетка не «Мои файлы».
* **Никакой горизонтальной прокрутки.** Названия переносятся, кнопки порядка — 44×44.
* **Файлы — плиткой, папки — деревом (155).** Плитка — `files_grid.py`, дерево —
  `files_tree.py`, загрузка и ссылка — `files_upload.py` и `files_link.py`: порог
  `test_webapp_pages_split.py` — 400 строк на модуль страниц.

Загрузка идёт мимо сервера: `presign → PUT в хранилище → commit`. Поэтому тут нет
`<form>` и `multipart`: телефон отдаёт файл прямо в объектное хранилище, а сервер
выдаёт ссылку и записывает запись в базу.

Обоснование: docs/development/192 §8, docs/development/155 §2.2, docs/development/92 §9.
"""

from __future__ import annotations

import json

from app.file_kind import CHIP_LABELS, KIND_WORDS
from app.pages import (
    files_actions,
    files_file,
    files_folder,
    files_grid,
    files_link,
    files_tree,
    files_upload,
    layout,
)
from app.pages.admin_core import CORE


def files_kind_words() -> str:
    """Слова чипов из одного списка: `file_kind.KIND_WORDS` уезжает в JS как данные."""
    return json.dumps(list(KIND_WORDS), ensure_ascii=False)


def files_chip_labels() -> str:
    """Надписи чипов из одной таблицы: «Документы», «Текст», «Картинки», «Аудио»."""
    return json.dumps(dict(CHIP_LABELS), ensure_ascii=False)

_BODY_FILES = """
<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Место</h2>
    <span class="ui-section__meta" id="quota-text">считаю</span>
  </div>
  <div class="ui-meter"><span class="ui-meter__fill" id="quota-fill"></span></div>
  <p class="ui-small ui-muted files-hint">Крупные файлы идут мимо Telegram: у бота лимит 20 МБ.</p>
</section>

""" + files_upload.BODY + files_link.BODY + """

<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title" id="list-title">Мои файлы</h2>
    <span class="ui-section__meta" id="count"></span>
  </div>
  <div class="ui-field">
    <label class="ui-field__label" for="q">Поиск</label>
    <input class="ui-input" id="q" type="search" maxlength="200"
           placeholder="Имя, папка или тип" autocomplete="off">
  </div>
  <div class="ui-actions files-chips" id="chips"></div>
  <p class="ui-small ui-muted files-hint" id="list-note" hidden></p>
  <div class="ui-grid" id="list"></div>
</section>
"""

_SCRIPT_FILES = r"""
  var chosen = [];
  var denied = false;
  // Список файлов и фильтр «Без папки» (V-065). Фильтр ставит строка дерева
  // (`files_tree.py`), поэтому состояние живёт здесь, рядом с отрисовкой сетки,
  // а строка только переключает его и зовёт `renderList`.
  var allFiles = [];
  var noFolderOnly = false;
  // Поиск (docs/development/192 §8.3): любой активный фильтр переводит сетку на
  // `/webapp/files/search`, пустой набор возвращает `/webapp/files/list`.
  var searchQ = "";
  var searchKind = "";
  var searchStorage = "";
  var searchTimer = null;

  function humanSize(bytes) {
    var value = Number(bytes) || 0;
    if (value >= 1024 * 1024 * 1024) {
      return (value / 1024 / 1024 / 1024).toFixed(1).replace(".", ",") + "\u00a0ГБ";
    }
    if (value >= 1024 * 1024) {
      return (value / 1024 / 1024).toFixed(1).replace(".", ",") + "\u00a0МБ";
    }
    if (value >= 1024) return Math.round(value / 1024) + "\u00a0КБ";
    return value + "\u00a0Б";
  }

  function mmss(seconds) {
    if (!seconds) return "";
    var total = Math.round(Number(seconds));
    return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
  }

  function renderQuota(quota) {
    quota = quota || { used: 0, total: 0 };
    var percent = quota.total ? Math.min(100, 100 * quota.used / quota.total) : 0;
    $("quota-text").textContent = humanSize(quota.used) + " из " + humanSize(quota.total);
    var fill = $("quota-fill");
    fill.style.width = percent.toFixed(1) + "%";
    fill.className = "ui-meter__fill" + (percent >= 90 ? " ui-meter__fill--danger"
      : percent >= 75 ? " ui-meter__fill--warn" : "");
  }

  function searchActive() {
    return Boolean(searchQ || searchKind || searchStorage);
  }

  function visibleFiles() {
    return allFiles.filter(function (file) {
      return !noFolderOnly || fileWithoutFolder(file);
    });
  }

  // Пояснение под заголовком: без него сузившаяся сетка выглядит как пропавшие файлы.
  function syncNoFolderNote() {
    var note = $("list-note");
    note.hidden = !noFolderOnly || searchActive();
    note.textContent = note.hidden ? ""
      : "Показаны файлы без папки (" + visibleFiles().length + "). "
        + "Нажмите строку «Без папки» ещё раз, чтобы увидеть все.";
  }

  function renderList() {
    var rows = searchActive() ? allFiles : visibleFiles();
    var list = $("list");
    clear(list);
    $("list-title").textContent = searchActive() ? "Найдено: " + rows.length : "Мои файлы";
    $("count").textContent = rows.length
      ? rows.length + " " + plural(rows.length, "файл", "файла", "файлов") : "";
    if (!rows.length) {
      if (searchActive()) {
        list.appendChild(empty("Ничего не нашлось",
          "Ищите по имени, папке и типу. Описание и содержимое файла в поиск не входят."));
      } else if (noFolderOnly) {
        list.appendChild(empty("Файлов без папки нет",
          "Все файлы разложены по папкам. Нажмите «Без папки» ещё раз, чтобы увидеть все."));
      } else {
        list.appendChild(empty("Файлов пока нет",
          "Загрузите первый — он появится здесь и в папке, если вы её указали."));
      }
      syncNoFolderNote();
      return;
    }
    rows.forEach(function (file) { list.appendChild(fileTile(file)); });
    // Широкая плитка выбирается по раскладке, а не по длине имени: после вставки
    // браузер уже знает, какое имя не влезло в три строки (155 §2.2).
    fitTiles(list);
    syncNoFolderNote();
  }

  function renderFiles(data) {
    renderQuota(data && data.quota);
    allFiles = (data && data.data) || [];
    renderList();
    // Число в строке «Без папки» считается по этому же списку: обновляем его после
    // загрузки и удаления, не перерисовывая дерево и не сбивая раскрытие папок.
    syncNoFolderRow();
  }

  // Один вход на список и поиск: активные фильтры идут в `/search`, пустые — в `/list`.
  function refresh() {
    var url = "/webapp/files/list";
    if (searchActive()) {
      var parts = [];
      if (searchQ) parts.push("q=" + encodeURIComponent(searchQ));
      if (searchKind) parts.push("kind=" + encodeURIComponent(searchKind));
      if (searchStorage) parts.push("storage=" + encodeURIComponent(searchStorage));
      url = "/webapp/files/search?" + parts.join("&");
    }
    return api(url).then(function (res) {
      if (res.status === 403) {
        denied = true;
        if ($("upload")) $("upload").disabled = true;
        if ($("pick")) $("pick").disabled = true;
        status("Раздел доступен администратору, организатору и мастеру.", true);
        clear($("list"));
        $("list").appendChild(empty("Раздел не для вас",
          "Файлы студии открыты администратору, организатору и мастеру."));
        $("count").textContent = "";
        return null;
      }
      if (res.status !== 200) {
        // Своей кнопки «Повторить» нет: плашка `networkGuard` оболочки уже даёт её
        // и на обрыве, и на 5xx. Экран говорит только, чего нет в этом месте.
        clear($("list"));
        $("count").textContent = "";
        $("list").appendChild(empty("Файлы не загрузились",
          res.status === 0 || res.status >= 500
            ? "Сервер не ответил. Повторите, когда появится связь."
            : problem(res)));
        return null;
      }
      if (searchActive()) {
        // Поиск не отдаёт полосу места: она не меняется от запроса, а сервер её не считает.
        renderListFromSearch(res.body);
        return res.body;
      }
      renderFiles(res.body);
      return res.body;
    });
  }

  function renderListFromSearch(body) {
    allFiles = (body && body.data) || [];
    clear($("list"));
    $("list-title").textContent = "Найдено: " + allFiles.length;
    $("count").textContent = "";
    if (!allFiles.length) {
      $("list").appendChild(empty("Ничего не нашлось",
        "Ищите по имени, папке и типу. Описание и содержимое файла в поиск не входят."));
      return;
    }
    allFiles.forEach(function (file) { $("list").appendChild(fileTile(file)); });
    fitTiles($("list"));
  }

  // --- чипы: Все · Документы · Текст · Картинки · Аудио · Ссылки -----------------
  // Подписи чипов — отдельная таблица, а не слово + «ы» (§8.3): она приходит
  // с сервера из `file_kind.CHIP_LABELS`, одна на экран «Файлы» и «Папка».
  function chips() {
    var holder = $("chips");
    clear(holder);
    var items = [{ label: "Все", kind: "", storage: "" }];
    fileKindWords.forEach(function (word) {
      items.push({ label: fileChipLabels[word] || word, kind: word, storage: "" });
    });
    items.push({ label: "Ссылки", kind: "", storage: "drive" });
    items.forEach(function (item) {
      var chip = el("button", "ui-btn ui-btn--sm", item.label);
      chip.type = "button";
      chip.dataset.kind = item.kind;
      chip.dataset.storage = item.storage;
      if (item.kind === searchKind && item.storage === searchStorage) {
        chip.classList.add("ui-btn--primary");
      }
      chip.addEventListener("click", function () {
        // «Ссылки» и чип типа взаимоисключающи: «документ» + «drive» — это пересечение,
        // которого в ряду нет; выбранный чип задаёт набор целиком.
        searchKind = item.kind;
        searchStorage = item.storage;
        chips();
        refresh();
      });
      holder.appendChild(chip);
    });
  }

  function onQuery() {
    var value = ($("q").value || "").trim();
    if (value === searchQ) return;
    searchQ = value;
    if (searchTimer) clearTimeout(searchTimer);
    // Пауза, пока человек печатает: запрос на букву — это шум и лишние 403 в логах.
    searchTimer = setTimeout(function () { refresh(); }, 250);
  }

  $("q").addEventListener("input", onQuery);
  $("q").addEventListener("change", onQuery);

  auth().then(function (denied) {
    if (denied) { status(denied, true); return; }
    chips();
    refresh().then(function (data) {
      if (!data && !searchActive()) return;
      loadFolders();
      loadFolderOptions();
      loadTags();
    });
  });
"""

FILES_PAGE = layout.page(
    "Файлы", _BODY_FILES + files_actions.BODY, back="/webapp/more", head=files_actions.STYLE,
    scripts="<script>\n" + CORE + "var fileKindWords = " + files_kind_words() + ";\n"
            + "var fileChipLabels = " + files_chip_labels() + ";\n"
            + files_upload.SCRIPT + files_link.SCRIPT
            + files_grid.ICON_FOR + files_grid.SCRIPT
            + files_tree.TREE_BUILD + files_tree.SCRIPT
            + _SCRIPT_FILES + files_actions.SCRIPT + "\n})();\n</script>")

# Экран папки живёт своим модулем (`files_folder.py`): порог ~400 строк на модуль страниц.
FOLDER_PAGE = files_folder.FOLDER_PAGE
# Экран одного файла — тоже свой модуль (`files_file.py`, А3).
FILE_PAGE = files_file.FILE_PAGE
