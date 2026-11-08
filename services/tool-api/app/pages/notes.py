"""Экран «Ещё → Заметки»: список, лист заметки, создание и правка (FR-F7).

Что здесь важнее всего:

* **Один путь записи.** Экран зовёт `/webapp/notes/save`, а тот — те же
  `create_note` / `update_note`, что и маршруты чата (`app/api/notes.py`). Правила
  видимости не дублируются на клиенте: сервер не присылает чужую личную заметку вовсе.
* **Чужая личная не правится.** У такой заметки в ответе `editable: false`, кнопки
  «Изменить» нет; повторная попытка всё равно получит 404 от эндпоинта — страница лишь
  не показывает лишнего.
* **Лист заметки — нижний лист дизайн-системы.** Длинный текст читается целиком,
  а не обрезается в строке списка; строки сохраняют переносы (`white-space: pre-wrap`).
* **Никаких вложений.** В заметке текст, заголовок и теги — файлы живут в своём разделе
  (П-7: граница раздела).

Данные — `/webapp/notes/list`, сохранение — `/webapp/notes/save`; оба за правом
`notes.write` (у всех действующих ролей оно есть).
"""

from __future__ import annotations

from app.pages import layout
from app.pages.admin_core import CORE

_STYLE = """<style>
  .notes-body { white-space: pre-wrap; overflow-wrap: anywhere; }
  .notes-tags { display: flex; flex-wrap: wrap; gap: var(--ui-s1); margin-top: var(--ui-s2); }
  .notes-row__text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
"""

_BODY_NOTES = """
<section class="ui-section" id="form-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title" id="form-title">Новая заметка</h2>
  </div>
  <div class="ui-card">
    <div class="ui-field">
      <label class="ui-field__label" for="title">Заголовок</label>
      <input class="ui-input" id="title" maxlength="120" autocomplete="off"
             placeholder="можно не заполнять">
      <span class="ui-field__hint">Если пусто — возьму первую строку текста.</span>
    </div>
    <div class="ui-field">
      <label class="ui-field__label" for="body">Текст</label>
      <textarea class="ui-textarea" id="body" maxlength="4000"
                placeholder="что нужно запомнить"></textarea>
    </div>
    <div class="ui-field">
      <label class="ui-field__label" for="tags">Теги</label>
      <input class="ui-input" id="tags" autocomplete="off"
             placeholder="через запятую, например: реквизит, зал">
    </div>
    <div class="ui-field">
      <span class="ui-field__label">Кому видна</span>
      <div class="ui-segmented" id="scope">
        <button class="ui-segmented__item ui-segmented__item--on" type="button"
                data-scope="personal" aria-pressed="true">Только мне</button>
        <button class="ui-segmented__item" type="button"
                data-scope="shared" aria-pressed="false">Всем</button>
      </div>
    </div>
    <div class="ui-actions">
      <button class="ui-btn ui-btn--primary ui-btn--block" type="button" id="save">Сохранить</button>
      <button class="ui-btn ui-btn--block" type="button" id="cancel" hidden>Отменить правку</button>
    </div>
    <span class="ui-field__error" id="error" hidden></span>
  </div>
</section>

<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Заметки</h2>
    <span class="ui-section__meta" id="count"></span>
  </div>
  <div class="ui-list" id="list"></div>
</section>
<div class="ui-small ui-muted" id="status"></div>
"""

_SCRIPT_NOTES = r"""
  var notes = [];
  var editingId = null;
  var scope = "personal";

  function tagsOf(text) {
    return String(text || "").split(",").map(function (item) {
      return item.trim().replace(/^#/, "").trim();
    }).filter(Boolean);
  }

  function setScope(value) {
    scope = value === "shared" ? "shared" : "personal";
    Array.prototype.forEach.call(document.querySelectorAll("#scope .ui-segmented__item"),
      function (button) {
        var on = button.getAttribute("data-scope") === scope;
        button.classList.toggle("ui-segmented__item--on", on);
        button.setAttribute("aria-pressed", on ? "true" : "false");
      });
  }

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function noteRow(note) {
    var button = el("button", "ui-row");
    button.type = "button";
    button.setAttribute("data-id", String(note.id));
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title notes-row__text", note.title));
    var meta = [note.scope_title];
    if (!note.mine) { meta.push("автор: " + note.author); }
    meta.push(when(note.updated_at));
    main.appendChild(el("span", "ui-row__meta", meta.join(" · ")));
    button.appendChild(main);
    button.appendChild(el("span", "ui-row__chevron", "\u203a"));
    button.addEventListener("click", function () { openNote(note); });
    return button;
  }

  function render() {
    var list = $("list");
    clear(list);
    $("count").textContent = notes.length
      ? notes.length + " " + plural(notes.length, "заметка", "заметки", "заметок") : "";
    if (!notes.length) {
      list.appendChild(empty("Заметок пока нет",
        "Напишите первую — она появится здесь и в чате по команде /note."));
      return;
    }
    notes.forEach(function (note) { list.appendChild(noteRow(note)); });
  }

  function sheetBody(note) {
    var box = el("div");
    if (note.tags && note.tags.length) {
      var tags = el("div", "notes-tags");
      note.tags.forEach(function (tag) { tags.appendChild(el("span", "ui-chip", tag)); });
      box.appendChild(tags);
    }
    box.appendChild(el("p", "ui-text ui-muted",
      note.scope_title + (note.mine ? "" : " · автор: " + note.author)
      + (note.updated_at ? " · " + when(note.updated_at, true) : "")));
    box.appendChild(el("p", "ui-text notes-body", note.body));
    return box;
  }

  function openNote(note) {
    var actions = [{ label: "Закрыть" }];
    if (note.editable) {
      actions.unshift({ label: "Изменить", primary: true, onTap: function () { editNote(note); } });
    }
    SVOI.sheet({ title: note.title, body: sheetBody(note), actions: actions });
  }

  function editNote(note) {
    editingId = note.id;
    $("form-title").textContent = "Правка заметки";
    $("title").value = note.title;
    $("body").value = note.body;
    $("tags").value = (note.tags || []).join(", ");
    setScope(note.scope);
    $("save").textContent = "Сохранить изменения";
    $("cancel").hidden = false;
    showError("");
    $("form-section").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function resetForm() {
    editingId = null;
    $("form-title").textContent = "Новая заметка";
    $("title").value = "";
    $("body").value = "";
    $("tags").value = "";
    setScope("personal");
    $("save").textContent = "Сохранить";
    $("cancel").hidden = true;
    showError("");
  }

  function load() {
    return api("/webapp/notes/list").then(function (res) {
      if (res.status === 401) {
        status("Откройте приложение из бота — сессия не подтверждена.", true);
        return;
      }
      if (res.status !== 200) { status(problem(res), true); return; }
      notes = res.body.notes || [];
      status("");
      render();
    });
  }

  function save() {
    var body = $("body").value.trim();
    if (!body) {
      showError("Напишите текст заметки.");
      $("body").focus();
      return;
    }
    showError("");
    var button = $("save");
    button.disabled = true;
    var payload = {
      title: $("title").value.trim(),
      body: body,
      tags: tagsOf($("tags").value),
      scope: scope
    };
    if (editingId) { payload.id = editingId; }
    // Внимание: второй аргумент `api()` — либо тело запроса, либо набор опций fetch.
    // У заметки есть поле `body`, и объект с ключом `body` оболочка принимает за опции
    // fetch (уходит GET с телом, запрос падает) — поэтому передаём опции явно.
    api("/webapp/notes/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (res) {
      button.disabled = false;
      if (res.status === 200 && res.body.ok) {
        toast(editingId ? "Заметка изменена" : "Заметка сохранена");
        resetForm();
        load();
        return;
      }
      showError(problem(res));
    }, function () {
      button.disabled = false;
      showError("Нет связи. Проверьте сеть и повторите.");
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll("#scope .ui-segmented__item"),
    function (button) {
      button.addEventListener("click", function () {
        setScope(button.getAttribute("data-scope"));
      });
    });

  $("save").addEventListener("click", save);
  $("cancel").addEventListener("click", resetForm);

  auth().then(function (denied) {
    if (denied) { status(denied, true); return; }
    load();
  });
"""

NOTES_PAGE = layout.page("Заметки", _BODY_NOTES, back="/webapp/more", head=_STYLE,
                         scripts="<script>\n" + CORE + _SCRIPT_NOTES + "\n})();\n</script>")
