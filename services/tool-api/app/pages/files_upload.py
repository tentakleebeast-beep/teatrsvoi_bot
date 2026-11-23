"""Экран «Файлы»: загрузка — место, папка, описание и очередь (E4, V-124).

До E4 загрузка не спрашивала папку и клала всё в `Личное/<вид>`, а описания не было
вовсе. Здесь три поля, которые решают, куда и чем файл станет:

* переключатель места «В студию / Ссылка на Drive» — форма ссылки живёт в `files_link.py`;
* «Куда» — общие папки путями и свои личные; первый пункт «Личное — по виду файла»
  оставляет поведение прежнего дня (FR-K11);
* «Что это» — описание по желанию, одно на партию (V-124): текст уходит в каждый файл,
  потом правится у каждого отдельно.

Вынесено из `files.py` отдельным модулем: порог `test_webapp_pages_split.py` — 400 строк
на модуль страниц. Помощники (`$`, `el`, `api`, `problem`, `status`, `refresh`) приходят
из общего замыкания страницы — скрипт просто подклеивается.

Обоснование: docs/development/192 §8.1, §8.7.
"""

from __future__ import annotations

# Разметка. `#link-panel` живёт в `files_link.py`; здесь — выбор файлов.
BODY = """
<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Загрузить</h2></div>
  <div class="ui-actions files-actions" id="place-switch">
    <button class="ui-btn ui-btn--sm" type="button" id="place-studio"
            aria-pressed="true">В студию</button>
    <button class="ui-btn ui-btn--sm" type="button" id="place-drive"
            aria-pressed="false">Ссылка на Drive</button>
  </div>
  <div id="upload-panel">
    <div class="ui-field">
      <label class="ui-field__label" for="folder-pick">Куда</label>
      <select class="ui-input" id="folder-pick">
        <option value="">Личное — по виду файла</option>
      </select>
    </div>
    <div class="ui-field">
      <label class="ui-field__label" for="describe">Что это</label>
      <textarea class="ui-input" id="describe" rows="2" maxlength="500"></textarea>
      <span class="ui-field__hint">Необязательно. Поиск по описанию не идёт — ищет по имени,
        папке и типу.</span>
    </div>
    <p class="ui-small ui-muted" id="shared-note" hidden>В общую папку: личное место не расходуется.</p>
    <p class="ui-small ui-muted" id="picked">Файлы не выбраны</p>
    <div class="ui-meter" id="bar" hidden><span class="ui-meter__fill" id="bar-fill"></span></div>
    <div class="ui-actions files-actions">
      <button class="ui-btn ui-btn--block" type="button" id="pick">Выбрать файлы</button>
      <button class="ui-btn ui-btn--primary ui-btn--block" type="button" id="upload" disabled>Загрузить</button>
    </div>
    <p class="ui-small ui-muted" id="status"></p>
    <input id="file" type="file" multiple hidden>
  </div>
</section>
"""

SCRIPT = r"""
  // --- место загрузки: в студию или ссылкой на Drive (docs/development/192 §8.1) ---
  var uploadPlace = "studio";
  var folderScopes = {};
  var folderOptions = [];

  function setPlace(place) {
    uploadPlace = place;
    var studio = $("place-studio");
    var drive = $("place-drive");
    var panel = $("upload-panel");
    var link = $("link-panel");
    studio.classList.toggle("ui-btn--primary", place === "studio");
    drive.classList.toggle("ui-btn--primary", place === "drive");
    studio.setAttribute("aria-pressed", place === "studio" ? "true" : "false");
    drive.setAttribute("aria-pressed", place === "drive" ? "true" : "false");
    panel.hidden = place !== "studio";
    if (link) link.hidden = place !== "drive";
  }

  // Выбранная папка: `null` — «Личное — по виду файла», папку выбирает сервер (FR-K11).
  function pickedFolder() {
    var value = ($("folder-pick").value || "").trim();
    return /^\d+$/.test(value) ? Number(value) : null;
  }

  // Полоса личного места не должна врать: в общую папку личное место не расходуется.
  function syncSharedNote() {
    var note = $("shared-note");
    var id = pickedFolder();
    note.hidden = !(id && folderScopes[id] === "shared");
  }

  function fillFolderSelect(select, emptyLabel) {
    if (!select) return;
    var keep = select.value;
    clear(select);
    var none = el("option", null, emptyLabel);
    none.value = "";
    select.appendChild(none);
    folderOptions.forEach(function (folder) {
      var option = el("option", null, folder.path);
      option.value = String(folder.id);
      select.appendChild(option);
    });
    select.value = keep;
  }

  // Папки — двумя вызовами (`scope=shared`, `scope=personal`): дерево отдаёт оба списка,
  // нового параметра на сервере для этого не нужно (§6.11).
  function loadFolderOptions() {
    return Promise.all([
      api("/webapp/folders?scope=shared"),
      api("/webapp/folders?scope=personal")
    ]).then(function (answers) {
      folderOptions = [];
      folderScopes = {};
      answers.forEach(function (res, index) {
        if (res.status !== 200) return;
        (res.body.data || []).forEach(function (folder) {
          folderOptions.push(folder);
          folderScopes[folder.id] = index === 0 ? "shared" : "personal";
        });
      });
      fillFolderSelect($("folder-pick"), "Личное — по виду файла");
      fillFolderSelect($("link-folder"), "Без папки");
      syncSharedNote();
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

  function describeValue() {
    return ($("describe").value || "").trim();
  }

  function uploadOne(file) {
    var mime = file.type || "application/octet-stream";
    var folderId = pickedFolder();
    var description = describeValue();
    return api("/webapp/files/presign", {
      name: file.name, mime: mime, size: file.size, folder_id: folderId
    }).then(function (res) {
      if (res.status !== 200) throw new Error(problem(res));
      var slot = res.body;
      // В хранилище файл уходит напрямую: Telegram-инициализация тут не нужна.
      return fetch(slot.upload_url, { method: "PUT", headers: { "Content-Type": mime }, body: file })
        .then(function (put) {
          if (!put.ok) throw new Error("Хранилище не приняло файл: код " + put.status + ".");
          $("bar-fill").style.width = "100%";
          return sha256Hex(file).then(function (hash) {
            return api("/webapp/files/commit", {
              object_key: slot.object_key, name: file.name, mime: mime, sha256: hash,
              folder_id: folderId, description: description || null
            });
          });
        })
        .then(function (commit) {
          if (commit.status !== 200) throw new Error(problem(commit));
          return commit.body;
        });
    });
  }

  $("place-studio").addEventListener("click", function () { setPlace("studio"); });
  $("place-drive").addEventListener("click", function () { setPlace("drive"); });
  $("folder-pick").addEventListener("change", syncSharedNote);
  setPlace("studio");

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
"""
