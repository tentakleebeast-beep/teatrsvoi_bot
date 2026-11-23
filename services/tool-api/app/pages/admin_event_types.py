"""Экран «Типы и цвета событий» (docs/development/190 §6, дизайн 187 §5).

Зачем: типы событий были зашиты в три словаря кода, цвета — ещё и в YAML. Экран
даёт организатору одну настройку: буква метки, название, цвет, порядок и видимость.
Код типа сервер собирает сам, буква после создания не меняется — это оформление
поверх стабильного ключа, а не сам ключ.

Адрес `/webapp/admin/t/event-types`, а не `/webapp/events/types`: второй занят
JSON-эндпоинтом, и страница с тем же путём перехватила бы его ответ (тот же дефект
был у `/webapp/admin/invites`, 30.10.2026).

Страница публична: HTML — константа, поэтому форма отдаётся скрытой и показывается
только после ответа `GET /webapp/events/types` со 200. Роль без `calendar.write`
получает 403 и видит объяснение, а не кнопку сохранения.

Диалог сохранения — три равноправные кнопки без значения по умолчанию (V-087):
«Перекрасить все», «Только новые», «Отмена». Закрытие листа = отмена.
"""

from __future__ import annotations

from app.pages.admin_core import screen

_HEAD_EVENT_TYPES = (
    "<style>\n"
    "  #types-form { display: flex; flex-direction: column; gap: var(--ui-s4); }\n"
    "  #types-form[hidden], #denied[hidden] { display: none; }\n"
    "  .ev-type-row { display: grid; grid-template-columns: 44px 1fr auto;\n"
    "                 gap: var(--ui-s2); align-items: center; }\n"
    "  .ev-type-row .ui-input { min-width: 0; }\n"
    "  .ev-letter { text-align: center; text-transform: uppercase; }\n"
    "</style>"
)

_BODY_EVENT_TYPES = """
<div class="ui-small ui-muted" id="status" role="status">Проверяю доступ…</div>

<section class="ui-section" id="denied" hidden>
  <div class="ui-empty">
    <div class="ui-empty__title">Типы и цвета — у организатора</div>
    <div class="ui-empty__text">Буквы, цвета и порядок типов событий задаёт тот, кто
      ведёт календарь. У вашей роли этой настройки нет.</div>
  </div>
</section>

<div id="types-form" hidden>
  <p class="ui-small ui-muted">Буква ставится в начало заголовка события: «[С] Спектакль».
    После создания типа буква не меняется — меняются название, цвет и порядок.</p>
  <div class="ui-list" id="types"></div>
  <section class="ui-section" id="hidden-block" hidden>
    <div class="ui-section__head"><h2 class="ui-section__title">Скрытые</h2></div>
    <div class="ui-small ui-muted">Эти типы не предлагаются при создании события:
      старые события сохраняют свой тип.</div>
    <div class="ui-list" id="types-hidden"></div>
  </section>
  <button class="ui-btn ui-btn--primary ui-btn--block" id="save" type="button">Сохранить</button>
  <div class="ui-alert ui-alert--ok" id="done" hidden></div>
</div>

<div class="ui-alert" id="error" hidden></div>
"""

_SCRIPT_EVENT_TYPES = r"""
  var palette = [];
  var types = [];
  var mode = "transitional";

  var COLOR_NAMES = {};

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function showDone(text) {
    var box = $("done");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function showDenied() {
    $("types-form").hidden = true;
    $("denied").hidden = false;
    status("");
  }

  function showForm() {
    $("denied").hidden = true;
    $("types-form").hidden = false;
  }

  function colorOptions(current) {
    var html = "";
    palette.forEach(function (color) {
      COLOR_NAMES[color.color_id] = color.name;
      html += '<option value="' + color.color_id + '"' +
        (String(color.color_id) === String(current) ? " selected" : "") + ">" +
        color.color_id + " · " + color.name + "</option>";
    });
    if (current === null || current === undefined) {
      html = '<option value="" selected>без цвета</option>' + html;
    }
    return html;
  }

  function rowOf(item, index) {
    var row = document.createElement("div");
    row.className = "ui-card ev-type-row";
    row.setAttribute("data-code", item.code || "");
    row.setAttribute("data-index", String(index));
    row.innerHTML =
      '<input class="ui-input ev-letter" maxlength="1" aria-label="Буква"' +
      (item.letter ? ' value="' + item.letter + '"' : ' value=""') + ">" +
      '<input class="ui-input ev-title" maxlength="40" aria-label="Название" value="' +
      (item.title || "") + '">' +
      '<select class="ui-input ev-color" aria-label="Цвет">' + colorOptions(item.color_id) +
      "</select>" +
      '<input class="ui-input ev-order" inputmode="numeric" maxlength="4" aria-label="Порядок" value="' +
      String(item.sort_order || 0) + '">' +
      '<label class="ui-switch-row ev-active' + (item.is_active ? " ui-switch-row--on" : "") +
      '" aria-pressed="' + (item.is_active ? "true" : "false") + '">' +
      '<span class="ui-row__main"><span class="ui-row__title">Виден при создании</span></span>' +
      '<span class="ui-switch"></span></label>';
    var letter = row.querySelector(".ev-letter");
    // Буква существующего типа неизменяема (409 letter_immutable): сервер это
    // проверяет, а поле не даёт случайно стереть метку у старых событий.
    if (item.code) { letter.readOnly = true; }
    var toggle = row.querySelector(".ev-active");
    toggle.addEventListener("click", function (event) {
      event.preventDefault();
      var on = !toggle.classList.contains("ui-switch-row--on");
      toggle.classList.toggle("ui-switch-row--on", on);
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
    });
    return row;
  }

  function render(body) {
    palette = body.palette || [];
    COLOR_NAMES = {};
    types = body.types || [];
    mode = body.mode || "transitional";
    // Неактивные типы — отдельным блоком «Скрытые» (190 §6.2): строка та же
    // (название и цвет правятся), но видно, что при создании их не предлагают.
    var list = $("types");
    var hiddenList = $("types-hidden");
    list.innerHTML = "";
    hiddenList.innerHTML = "";
    var hiddenCount = 0;
    types.forEach(function (item, index) {
      var row = rowOf(item, index);
      if (item.is_active) { list.appendChild(row); }
      else { hiddenList.appendChild(row); hiddenCount += 1; }
    });
    $("hidden-block").hidden = hiddenCount === 0;
  }

  function collect() {
    var rows = document.querySelectorAll("#types-form .ev-type-row");
    var out = [];
    for (var index = 0; index < rows.length; index += 1) {
      var row = rows[index];
      var code = row.getAttribute("data-code") || "";
      var color = row.querySelector(".ev-color").value;
      out.push({
        code: code || null,
        letter: (row.querySelector(".ev-letter").value || "").trim() || null,
        title: (row.querySelector(".ev-title").value || "").trim(),
        color_id: color ? Number(color) : null,
        sort_order: Number(row.querySelector(".ev-order").value || 0),
        is_active: row.querySelector(".ev-active").classList
          .contains("ui-switch-row--on")
      });
    }
    return out;
  }

  function problemText(res) {
    var body = res.body || {};
    var code = body.code || "";
    var texts = {
      letter_immutable: "Буква существующего типа не меняется.",
      letter_taken: "Такая буква уже занята другим типом.",
      letter_bad: "Буква — одна заглавная кириллическая.",
      title_bad: "Название — от 1 до 40 знаков.",
      color_bad: "Цвет — от 1 до 11.",
      color_required: "У активного типа должны быть буква и цвет.",
      order_bad: "Порядок — целое от 0 до 9999.",
      code_unknown: "Типа нет: обновите экран.",
      // Боевой календарь — только чтение (I-7): перекраска в целевом режиме
      // отказывает до сохранения, поэтому экран называет выход, а не «ошибку».
      recolor_target_forbidden: "В боевом календаре перекраска не включена. " +
        "Сохраните с «Только новые»."
    };
    if (texts[code]) { return texts[code]; }
    if (res.status === 403) { return "Типы и цвета настраивает организатор."; }
    if (res.status === 401) { return "Откройте приложение из бота — сессия не подтверждена."; }
    return "Сохранить не удалось. Попробуйте ещё раз.";
  }

  function describe(body) {
    var recolored = Number(body.recolored || 0);
    var total = Number(body.total || 0);
    var apply = body.apply || "";
    if (!apply) { return ""; }
    if (apply === "new") { return "Сохранено. Цвета применятся к новым событиям."; }
    if (total === 0) {
      return "Сохранено. Событий изменённых типов в календаре нет.";
    }
    var text = "Сохранено. Перекрашено " + recolored + " из " + total;
    if (recolored < total) {
      var first = (body.failed || [])[0];
      text += "; не удалось: " + (total - recolored) + " — " +
        (first ? (first.error || "ошибка") : "ошибка");
    }
    return text + "."
  }

  function save(apply) {
    showError("");
    showDone("");
    var button = $("save");
    button.disabled = true;
    SVOI.api("/webapp/events/types", { apply: apply, types: collect() })
      .then(function (res) {
        button.disabled = false;
        if (res.status === 403) { showDenied(); return; }
        if (res.status !== 200 || !res.body || !res.body.ok) {
          showError(problemText(res));
          return;
        }
        res.body.apply = apply;
        var text = describe(res.body);
        showDone(text);
        SVOI.toast(text);
      });
  }

  function ask() {
    // Три равноправные кнопки, ни одна не выбрана по умолчанию (V-087, 190 §6.4).
    SVOI.sheet({
      title: "Сохранить настройку?",
      body: '<p class="ui-text">Применить цвета к событиям, которые уже есть в календаре?</p>',
      actions: [
        {label: "Перекрасить все", id: "recolor-all", onTap: function () { save("all"); }},
        {label: "Только новые", id: "new-only", onTap: function () { save("new"); }},
        {label: "Отмена", id: "cancel"}
      ]
    });
  }

  function load() {
    SVOI.auth().then(function (denied) {
      if (denied) { status(denied, true); return; }
      SVOI.api("/webapp/events/types").then(function (res) {
        if (res.status === 403) { showDenied(); return; }
        if (res.status !== 200 || !res.body || !res.body.ok) {
          status("Не удалось открыть настройку: проверьте доступ.", true);
          return;
        }
        render(res.body);
        showForm();
        status("");
      });
    });
  }

  $("save").addEventListener("click", ask);
  load();
"""

EVENT_TYPES_PAGE = screen("Типы и цвета", _BODY_EVENT_TYPES, _SCRIPT_EVENT_TYPES,
                          head=_HEAD_EVENT_TYPES)
