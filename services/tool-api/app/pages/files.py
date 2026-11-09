"""Файлы и папки — на дизайн-системе v2 (docs/development/92 §9).

Что здесь важнее всего:

* **Кому открыто.** Решение V-039 (в): «Файлы студии» видят admin, org и master (право
  `files.access`, проверка на сервере); без права экран говорит словами (Б3). Кого
  пускает действие, решает домен: `folders` — admin и org, `tags`/`file_access` — admin.
* **Действия — на экране, а не только в чате.** Папки, доступ к личному файлу и
  утверждение тега живут в `files_actions.py`: экран и чат зовут один обработчик.
  Загрузка одна на оба входа — `presign → PUT → commit` (Ш-08).
* **Никакой горизонтальной прокрутки.** Названия переносятся, кнопки порядка —
  44×44, «Скачать архивом» — своей строкой под списком, а не в ряд с плеером.
* **Своё — только то, чего нет в системе:** полоса места и порядок треков на
  `.ui-meter` и `.ui-btn--icon`; всё прочее — классы `.ui-*`.

Загрузка идёт мимо сервера: `presign → PUT в хранилище → commit`. Поэтому тут нет
`<form>` и `multipart`: телефон отдаёт файл прямо в объектное хранилище, а сервер
выдаёт ссылку и записывает запись в базу.
"""

from __future__ import annotations

from app.pages import files_actions, files_file, files_folder, layout
from app.pages.admin_core import CORE

_BODY_FILES = """
<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Место</h2>
    <span class="ui-section__meta" id="quota-text">считаю</span>
  </div>
  <div class="ui-meter"><span class="ui-meter__fill" id="quota-fill"></span></div>
  <p class="ui-small ui-muted files-hint">Крупные файлы идут мимо Telegram: у бота лимит 20 МБ.</p>
</section>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Загрузить</h2></div>
  <p class="ui-small ui-muted" id="picked">Файлы не выбраны</p>
  <div class="ui-meter" id="bar" hidden><span class="ui-meter__fill" id="bar-fill"></span></div>
  <div class="ui-actions files-actions">
    <button class="ui-btn ui-btn--block" type="button" id="pick">Выбрать файлы</button>
    <button class="ui-btn ui-btn--primary ui-btn--block" type="button" id="upload" disabled>Загрузить</button>
  </div>
  <p class="ui-small ui-muted" id="status"></p>
  <input id="file" type="file" multiple hidden>
</section>

<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Мои файлы</h2>
    <span class="ui-section__meta" id="count"></span>
  </div>
  <div class="ui-list" id="list"></div>
</section>
"""

_SCRIPT_FILES = r"""
  var chosen = [];
  var denied = false;

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

  function fileRow(file) {
    var node = el("div", "ui-row");
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", file.name));
    var meta = [humanSize(file.size_bytes), kindWord(file.kind, file.name)];
    if (file.duration_sec) meta.push(mmss(file.duration_sec));
    if (file.folder) meta.push(file.folder);
    main.appendChild(el("span", "ui-row__meta", meta.join(" · ")));
    // Нажатие на имя открывает файл (А3): до этого строка была мёртвой зоной —
    // нажать можно было только «Удалить». Обработчик на `.ui-row__main`, а не на
    // строке: «Удалить» и шеврон папки остаются отдельными целями (150 §5.1).
    main.addEventListener("click", function () {
      location.href = "/webapp/file?id=" + file.id;
    });
    node.appendChild(main);
    // Удаление — только человек и кнопкой (FR-L8); подтверждение спрашивает лист.
    var actions = el("span", "ui-track__order");
    deleteFileButton(file, actions);
    node.appendChild(actions);
    if (file.folder_id) {
      var chevron = el("span", "ui-row__chevron", "\u203a");
      chevron.addEventListener("click", function (event) {
        event.stopPropagation();
        location.href = "/webapp/folder?id=" + file.folder_id;
      });
      node.appendChild(chevron);
    }
    return node;
  }

  function renderFiles(data) {
    renderQuota(data.quota);
    var rows = data.data || [];
    var list = $("list");
    clear(list);
    $("count").textContent = rows.length
      ? rows.length + " " + plural(rows.length, "файл", "файла", "файлов") : "";
    if (!rows.length) {
      list.appendChild(empty("Файлов пока нет",
        "Загрузите первый — он появится здесь и в папке, если вы её указали."));
      return;
    }
    rows.forEach(function (file) { list.appendChild(fileRow(file)); });
  }

  function refresh() {
    return api("/webapp/files/list").then(function (res) {
      if (res.status === 403) {
        denied = true;
        $("upload").disabled = true;
        $("pick").disabled = true;
        status("Раздел доступен администратору, организатору и мастеру.", true);
        clear($("list"));
        $("list").appendChild(empty("Раздел не для вас",
          "Файлы студии открыты администратору, организатору и мастеру."));
        $("count").textContent = "";
        return null;
      }
      if (res.status !== 200) { status(problem(res), true); return null; }
      renderFiles(res.body);
      return res.body;
    });
  }

  function sha256Hex(file) {
    if (!crypto.subtle) return Promise.resolve(null);
    return file.arrayBuffer().then(function (buffer) {
      return crypto.subtle.digest("SHA-256", buffer);
    }).then(function (digest) {
      return Array.from(new Uint8Array(digest))
        .map(function (byte) { return byte.toString(16).padStart(2, "0"); }).join("");
    }, function () { return null; });
  }

  function uploadOne(file) {
    var mime = file.type || "application/octet-stream";
    return api("/webapp/files/presign", { name: file.name, mime: mime, size: file.size })
      .then(function (res) {
        if (res.status !== 200) throw new Error(problem(res));
        var slot = res.body;
        // В хранилище файл уходит напрямую: Telegram-инициализация тут не нужна.
        return fetch(slot.upload_url, { method: "PUT", headers: { "Content-Type": mime }, body: file })
          .then(function (put) {
            if (!put.ok) throw new Error("Хранилище не приняло файл: код " + put.status + ".");
            $("bar-fill").style.width = "100%";
            return sha256Hex(file).then(function (hash) {
              return api("/webapp/files/commit", {
                object_key: slot.object_key, name: file.name, mime: mime, sha256: hash
              });
            });
          })
          .then(function (commit) {
            if (commit.status !== 200) throw new Error(problem(commit));
            return commit.body;
          });
      });
  }

  $("pick").addEventListener("click", function () { $("file").click(); });

  $("file").addEventListener("change", function () {
    chosen = Array.from($("file").files || []);
    $("picked").textContent = chosen.length
      ? chosen.map(function (file) { return file.name; }).join(", ")
      : "Файлы не выбраны";
    $("upload").disabled = !chosen.length || denied;
  });

  $("upload").addEventListener("click", function () {
    if (!chosen.length) { status("Сначала выберите файлы.", true); return; }
    var button = $("upload");
    button.disabled = true;
    $("bar").hidden = false;
    var queue = chosen.slice();
    var done = 0;

    function next() {
      var file = queue.shift();
      if (!file) {
        $("bar").hidden = true;
        $("bar-fill").style.width = "0";
        chosen = [];
        $("file").value = "";
        $("picked").textContent = "Файлы не выбраны";
        button.disabled = true;
        status(done ? "Загружено: " + done + "." : "", false);
        if (done) refresh();
        return;
      }
      status("Загружаю: " + file.name, false);
      uploadOne(file).then(function (result) {
        done += 1;
        var name = result.file ? result.file.name : file.name;
        var twice = result.file && result.file.duplicate ? " — такой файл уже был" : "";
        status("Готово: " + name + twice, false);
      }, function (error) {
        status(error && error.message ? error.message : "Не получилось загрузить.", true);
      }).then(next, next);
    }

    next();
  });

  auth().then(function (denied) {
    if (denied) { status(denied, true); return; }
    refresh().then(function (data) {
      if (!data) return;
      loadFolders();
      loadTags();
    });
  });
"""

FILES_PAGE = layout.page(
    "Файлы", _BODY_FILES + files_actions.BODY, back="/webapp/more", head=files_actions.STYLE,
    scripts="<script>\n" + CORE + files_actions.KIND_WORD + _SCRIPT_FILES
            + files_actions.SCRIPT + "\n})();\n</script>")

# Экран папки живёт своим модулем (`files_folder.py`): порог ~400 строк на модуль страниц.
FOLDER_PAGE = files_folder.FOLDER_PAGE
# Экран одного файла — тоже свой модуль (`files_file.py`, А3).
FILE_PAGE = files_file.FILE_PAGE

