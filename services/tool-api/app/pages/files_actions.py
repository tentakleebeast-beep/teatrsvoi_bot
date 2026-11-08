"""Экран «Файлы»: папки, доступ и теги — действия, а не только чтение (Ш-08).

Ф2 (доступ), Ф3 (тег) и Ф4 (папки) жили в чате и в API, но на экране входа не имели,
а `rename`, `delete` и `order` не звал никто — маршруты были сиротами
(`docs/reports/124` §3). Здесь у каждого действия появляется кнопка, и она идёт
в тот же обработчик, что и команда в боте: право и поведение не могут разъехаться.

Почему отдельным модулем: `files.py` держит загрузку и список файлов, этот — действия
над ними. Порог ~400 строк на модуль страниц проверяется тестом
(`tests/unit/test_webapp_pages_split.py`), и раздувать один файл нельзя.

Разметка и скрипт подставляются в общую страницу `files.FILES_PAGE`; помощники
(`$`, `el`, `api`, `problem`, `status`) приходят из CORE — скрипт выполняется в том же
замыкании, что и код страницы.
"""

from __future__ import annotations

# --- общее для экранов файлов -----------------------------------------------

# Единственное своё правило: подсказка под полосой места не должна отрываться
# от неё лишним отступом раздела.
STYLE = """<style>
  .files-hint { margin: 0; }
  .files-actions { margin-top: var(--ui-s2); }
</style>
"""

# «Папка» без `?id=`: пусто — не тупик (docs/development/91 §1 п.9, карта t_75149a6c).
# Вход в папку один — из «Файлов», поэтому пустое состояние называет источник и даёт
# кнопку-ссылку. Редирект не выбран: адрес `/webapp/folder` остаётся открывающимся
# (141 §2.4 — старые адреса не удаляются), а закладка не уводит в другой раздел молча.
EMPTY_FOLDER = """<div id="no-folder" hidden>
  <div class="ui-empty">
    <div class="ui-empty__title">Папка не выбрана</div>
    <div class="ui-empty__text">Папки открываются из «Файлов» — выберите нужную в списке «Мои файлы».</div>
    <div class="ui-actions files-actions">
      <a class="ui-btn ui-btn--block" href="/webapp/files">Открыть «Файлы»</a>
    </div>
  </div>
</div>
"""

# --- разметка ---------------------------------------------------------------

BODY = """
<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Папки</h2>
    <span class="ui-section__meta" id="folder-count"></span>
  </div>
  <div class="ui-list" id="folders"></div>
  <div class="ui-field">
    <label class="ui-field__label" for="folder-name">Новая папка</label>
    <input class="ui-input" id="folder-name" type="text" maxlength="100"
           placeholder="Реквизит или Спектакли/Пурга/шумы">
    <span class="ui-field__hint">Вложенную папку задайте путём через «/».</span>
  </div>
  <div class="ui-actions files-actions">
    <button class="ui-btn ui-btn--block" type="button" id="folder-create">Создать папку</button>
  </div>
  <p class="ui-small ui-muted" id="folder-status"></p>
</section>

<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Доступ к файлу</h2>
  </div>
  <p class="ui-small ui-muted files-hint">Личный файл открывается по причине и на срок; владелец получает уведомление.</p>
  <div class="ui-field">
    <label class="ui-field__label" for="access-file">Номер файла</label>
    <input class="ui-input" id="access-file" type="text" inputmode="numeric" placeholder="42">
  </div>
  <div class="ui-field">
    <label class="ui-field__label" for="access-reason">Причина</label>
    <input class="ui-input" id="access-reason" type="text" maxlength="500"
           placeholder="Сверка сметы за октябрь">
  </div>
  <div class="ui-actions files-actions">
    <button class="ui-btn ui-btn--block" type="button" id="access-grant">Открыть доступ</button>
    <button class="ui-btn ui-btn--block" type="button" id="access-revoke">Отозвать доступ</button>
  </div>
  <p class="ui-small ui-muted" id="access-status"></p>
  <div class="ui-list" id="access-list"></div>
</section>

<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Теги на утверждение</h2>
    <span class="ui-section__meta" id="tag-count"></span>
  </div>
  <div class="ui-list" id="tags"></div>
</section>
"""

# --- скрипт -----------------------------------------------------------------

SCRIPT = r"""
  // --- папки: создать, переименовать, закрыть (Ф4, Ш-08) ---------------------

  function folderNote(text, bad) {
    var box = $("folder-status");
    box.textContent = text || "";
    box.className = bad ? "ui-alert" : "ui-small ui-muted";
  }

  function folderRow(folder) {
    var node = el("div", "ui-row");
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", folder.name));
    main.appendChild(el("span", "ui-row__meta", folder.path));
    node.appendChild(main);

    var actions = el("span", "ui-track__order");

    var open = el("button", "ui-btn ui-btn--sm", "Открыть");
    open.type = "button";
    open.addEventListener("click", function () {
      location.href = "/webapp/folder?id=" + folder.id;
    });
    actions.appendChild(open);

    var rename = el("button", "ui-btn ui-btn--sm", "Имя");
    rename.type = "button";
    rename.setAttribute("aria-label", "Переименовать папку");
    rename.addEventListener("click", function () { askRename(node, folder); });
    actions.appendChild(rename);

    var remove = el("button", "ui-btn ui-btn--sm", "Закрыть");
    remove.type = "button";
    remove.setAttribute("aria-label", "Закрыть папку");
    var armed = false;
    remove.addEventListener("click", function () {
      if (!armed) {
        armed = true;
        remove.textContent = "Точно?";
        return;
      }
      closeFolder(folder);
    });
    actions.appendChild(remove);

    node.appendChild(actions);
    return node;
  }

  function askRename(node, folder) {
    var box = el("div", "ui-field");
    var input = el("input", "ui-input");
    input.type = "text";
    input.maxLength = 100;
    input.value = folder.name;
    var save = el("button", "ui-btn ui-btn--sm ui-btn--primary", "Сохранить");
    save.type = "button";
    save.addEventListener("click", function () {
      var name = (input.value || "").trim();
      if (!name) { folderNote("Название не может быть пустым.", true); return; }
      api("/webapp/folders/" + folder.id + "/rename", { name: name })
        .then(function (res) {
          if (res.status !== 200) { folderNote(problem(res), true); return; }
          folderNote("Папка переименована: " + res.body.path, false);
          loadFolders();
        });
    });
    var cancel = el("button", "ui-btn ui-btn--sm", "Отмена");
    cancel.type = "button";
    cancel.addEventListener("click", function () { loadFolders(); });
    box.appendChild(input);
    box.appendChild(save);
    box.appendChild(cancel);
    clear(node);
    node.appendChild(box);
  }

  function closeFolder(folder) {
    api("/webapp/folders/" + folder.id + "/delete", {})
      .then(function (res) {
        if (res.status !== 200) { folderNote(problem(res), true); return; }
        folderNote("Папка закрыта. Файлы остались — они в «Без папки».", false);
        loadFolders();
      });
  }

  function renderFolders(rows) {
    var list = $("folders");
    clear(list);
    $("folder-count").textContent = rows.length
      ? rows.length + " " + plural(rows.length, "папка", "папки", "папок") : "";
    if (!rows.length) {
      list.appendChild(empty("Папок пока нет",
        "Создайте первую — общие папки видят все, но правит только администратор."));
      return;
    }
    rows.forEach(function (folder) { list.appendChild(folderRow(folder)); });
  }

  function loadFolders() {
    return api("/webapp/folders").then(function (res) {
      if (res.status === 403) { renderFolders([]); return []; }
      if (res.status !== 200) { folderNote(problem(res), true); return []; }
      renderFolders(res.body.data || []);
      return res.body.data || [];
    });
  }

  function createFolder() {
    var raw = ($("folder-name").value || "").trim();
    if (!raw) { folderNote("Напишите название папки.", true); return; }
    var parts = raw.split("/").map(function (part) { return part.trim(); })
      .filter(function (part) { return part; });
    if (!parts.length) { folderNote("Напишите название папки.", true); return; }

    // Путь собирается по шагам, как в чате: промежуточные папки создаются сами,
    // и на экране, и в боте работает один и тот же обработчик.
    var parentId = null;
    var made = 0;
    var chain = Promise.resolve();
    parts.forEach(function (name) {
      chain = chain.then(function () {
        return api("/webapp/folders", { name: name, scope: "shared", parent_id: parentId })
          .then(function (res) {
            if (res.status !== 200) throw new Error(problem(res));
            parentId = res.body.id;
            made += 1;
            return res.body;
          });
      });
    });
    chain.then(function () {
      $("folder-name").value = "";
      folderNote("Готово: " + parts.join("/"), false);
      loadFolders();
    }, function (error) {
      folderNote(error && error.message ? error.message : "Папка не создалась.", true);
      if (made) loadFolders();
    });
  }

  // --- доступ к файлу: выдать и отозвать (Ф2, Ш-08) --------------------------

  function accessNote(text, bad) {
    var box = $("access-status");
    box.textContent = text || "";
    box.className = bad ? "ui-alert" : "ui-small ui-muted";
  }

  function accessFileId() {
    var value = ($("access-file").value || "").trim();
    return /^\d+$/.test(value) ? Number(value) : 0;
  }

  function renderGrants(rows) {
    var list = $("access-list");
    clear(list);
    if (!rows.length) {
      list.appendChild(empty("Доступ не открыт",
        "Пока никто, кроме владельца, этот файл не видит."));
      return;
    }
    rows.forEach(function (grant) {
      var node = el("div", "ui-row");
      var main = el("span", "ui-row__main");
      main.appendChild(el("span", "ui-row__title", grant.admin_name || "администратор"));
      main.appendChild(el("span", "ui-row__meta",
        (grant.reason || "без причины") + " · до " + String(grant.expires_at || "").slice(0, 16)));
      node.appendChild(main);
      list.appendChild(node);
    });
  }

  function loadGrants(fileId) {
    if (!fileId) { renderGrants([]); return; }
    api("/webapp/files/" + fileId + "/access").then(function (res) {
      if (res.status !== 200) { renderGrants([]); return; }
      renderGrants(res.body.data || []);
    });
  }

  function grantAccess() {
    var fileId = accessFileId();
    if (!fileId) { accessNote("Нужен номер файла — он виден в списке.", true); return; }
    var reason = ($("access-reason").value || "").trim();
    if (reason.length < 3) { accessNote("Без причины доступ не открывается.", true); return; }
    api("/webapp/files/" + fileId + "/access", { reason: reason })
      .then(function (res) {
        if (res.status !== 200) { accessNote(problem(res), true); return; }
        accessNote("Доступ открыт на " + res.body.ttl_hours + " ч. Владелец уведомлён.", false);
        loadGrants(fileId);
      });
  }

  function revokeAccess() {
    var fileId = accessFileId();
    if (!fileId) { accessNote("Нужен номер файла — он виден в списке.", true); return; }
    api("/webapp/files/" + fileId + "/access/revoke", {})
      .then(function (res) {
        if (res.status !== 200) { accessNote(problem(res), true); return; }
        accessNote("Доступ закрыт.", false);
        loadGrants(fileId);
      });
  }

  // --- теги: утвердить черновик (Ф3, Ш-08) -----------------------------------

  function renderTags(rows) {
    var list = $("tags");
    clear(list);
    $("tag-count").textContent = rows.length ? String(rows.length) : "";
    if (!rows.length) {
      list.appendChild(empty("Черновиков нет",
        "Новые теги участников появятся здесь — их утверждает администратор."));
      return;
    }
    rows.forEach(function (tag) {
      var node = el("div", "ui-row");
      var main = el("span", "ui-row__main");
      main.appendChild(el("span", "ui-row__title", tag.title));
      main.appendChild(el("span", "ui-row__meta",
        "черновик · файлов: " + (tag.uses || 0)));
      node.appendChild(main);
      var approve = el("button", "ui-btn ui-btn--sm ui-btn--primary", "Утвердить");
      approve.type = "button";
      approve.addEventListener("click", function () {
        api("/webapp/tags/" + tag.id + "/approve", {})
          .then(function (res) {
            if (res.status !== 200) { status(problem(res), true); return; }
            status("Тег «" + tag.title + "» утверждён.", false);
            loadTags();
          });
      });
      node.appendChild(approve);
      list.appendChild(node);
    });
  }

  function loadTags() {
    return api("/webapp/tags/pending").then(function (res) {
      if (res.status === 403) { renderTags([]); return; }
      if (res.status !== 200) { return; }
      renderTags(res.body.data || []);
    });
  }

  $("folder-create").addEventListener("click", createFolder);
  $("access-grant").addEventListener("click", grantAccess);
  $("access-revoke").addEventListener("click", revokeAccess);
  $("access-file").addEventListener("change", function () { loadGrants(accessFileId()); });
"""
