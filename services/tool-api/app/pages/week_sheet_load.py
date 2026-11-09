"""Строка «Загрузить из общего листа» и её лист предпросмотра (Э5, docs/development/149).

Почему отдельным модулем: `pages/week.py` собирает экран недели, а это действие
живёт по своим правилам — читает лист только на чтение (D2), ничего не пишет до
нажатия «Загрузить» (D4) и показывает расхождение базы с клеткой словами (D3).
Модуль держит разметку строки и скрипт листа; серверные состояния дня считает
`app/sheet_load.py`, экран их не пересчитывает.

Скрипт подклеивается в общее замыкание недели (`pages/week_sheet.py` открывает
IIFE, `pages/week_template.py` его закрывает), поэтому `$`, `api`, `plural`,
`toPayload`, `load` и `week` здесь те же, что у листа дня: своей копии нет.

Решение владельца по V-048 (09.11.2026): строка стоит вместо «Сохранить шаблон
недели», сама кнопка шаблона переехала ниже — в блок со сводкой и очисткой.
"""

from __future__ import annotations

#: Строка недели. Первая в нижнем списке: сводка, шаблон и очистка — ниже.
ROW_LOAD_FROM_SHEET = (
    '<button class="ui-row" type="button" id="load-from-sheet">'
    '<span class="ui-row__main">'
    '<span class="ui-row__title">Загрузить из общего листа</span>'
    '<span class="ui-row__meta">своя колонка из «Расписание студийцы»</span></span>'
    '<span class="ui-row__chevron">&#8250;</span></button>'
)

_SCRIPT_WEEK_SHEET_LOAD = r"""

  /* «Загрузить из общего листа» (Э5, docs/development/149 §6.2-6.3).
     Предпросмотр — GET, при открытии ничего не пишется; запись — только по
     основной кнопке и только выбранных дней. Состояния дня (new/differs/
     sheet_empty/unparsed) приходят с сервера: экран их не пересчитывает. */
  var sheetLoadState = null;

  function sheetLoadDays(count) {
    return count + " " + plural(count, "день", "дня", "дней");
  }

  function sheetLoadDay(item) {
    return item.weekday + " " + Number(String(item.day).slice(8, 10));
  }

  function sheetLoadMeta(item) {
    if (item.state === "unparsed") {
      return "«" + item.sheet_text + "» — не разобрано, загрузить нельзя";
    }
    if (item.state === "sheet_empty") {
      return "в листе пусто · у вас «" + item.db_text + "»";
    }
    if (item.state === "differs") {
      return "в листе «" + item.sheet_text + "» · у вас «" + item.db_text + "»";
    }
    return "в листе «" + item.sheet_text + "» · у вас не отмечено";
  }

  function sheetLoadRowText(item, row) {
    var main = document.createElement("span");
    main.className = "ui-row__main";
    var title = document.createElement("span");
    title.className = "ui-row__title";
    title.textContent = sheetLoadDay(item);
    var meta = document.createElement("span");
    meta.className = "ui-row__meta";
    meta.textContent = sheetLoadMeta(item);
    main.appendChild(title);
    main.appendChild(meta);
    row.appendChild(main);
  }

  function sheetLoadSwitchRow(item, on) {
    var row = document.createElement("button");
    row.type = "button";
    row.className = "ui-switch-row" + (on ? " ui-switch-row--on" : "");
    row.setAttribute("data-day", item.day);
    row.setAttribute("aria-pressed", on ? "true" : "false");
    sheetLoadRowText(item, row);
    var word = document.createElement("span");
    word.className = "ui-switch-row__state";
    word.textContent = on ? "загрузить" : "не трогать";
    row.appendChild(word);
    var mark = document.createElement("span");
    mark.className = "ui-switch";
    row.appendChild(mark);
    return row;
  }

  function sheetLoadInfoRow(item) {
    var row = document.createElement("div");
    row.className = "ui-row";
    row.setAttribute("data-day", item.day);
    sheetLoadRowText(item, row);
    return row;
  }

  function sheetLoadFresh() {
    /* new включён, day replaced only by choice: differs и sheet_empty выключены. */
    return { new: true, differs: false, sheet_empty: false, unparsed: false };
  }

  function sheetLoadToggle(item, row) {
    var state = sheetLoadState;
    if (!state || item.state === "unparsed") { return; }
    var on = !state.selected[item.day];
    state.selected[item.day] = on;
    row.classList.toggle("ui-switch-row--on", on);
    row.setAttribute("aria-pressed", on ? "true" : "false");
    row.querySelector(".ui-switch-row__state").textContent = on ? "загрузить" : "не трогать";
    sheetLoadPrimary();
  }

  function sheetLoadPrimary() {
    var state = sheetLoadState;
    if (!state || !state.primary) { return; }
    var count = state.order.filter(function (day) { return state.selected[day]; }).length;
    state.primary.textContent = count ? "Загрузить " + sheetLoadDays(count) : "Выберите дни";
    state.primary.disabled = count === 0;
  }

  function sheetLoadText(overlay, selector, text) {
    var node = overlay.querySelector(selector);
    if (node) { node.textContent = text; }
  }

  function sheetLoadFail(overlay, text) {
    var state = sheetLoadState;
    if (state && state.primary) { state.primary.style.display = "none"; }
    sheetLoadText(overlay, "#sheet-load-intro", text);
    var list = overlay.querySelector("#sheet-load-list");
    if (list) { list.textContent = ""; }
    sheetLoadText(overlay, "#sheet-load-same", "");
  }

  function sheetLoadWritable(item) {
    return item.state === "new" || item.state === "differs" || item.state === "sheet_empty";
  }

  function sheetLoadNothing(list) {
    var note = document.createElement("p");
    note.className = "ui-text";
    note.id = "sheet-load-nothing";
    note.textContent = "В листе и в приложении одно и то же — загружать нечего.";
    list.appendChild(note);
    var state = sheetLoadState;
    /* `hidden` не прячет: у `.ui-btn` свой `display`, и он сильнее атрибута. */
    if (state && state.primary) { state.primary.style.display = "none"; }
  }

  function sheetLoadRender(overlay, answer) {
    var state = sheetLoadState;
    if (!state) { return; }
    var tab = (answer.source && answer.source.spreadsheet) || "Расписание студийцы";
    sheetLoadText(overlay, "#sheet-load-intro",
      "«" + tab + "», ваша колонка " + answer.person.column
      + ". Ничего не изменится, пока не нажмёте «Загрузить».");

    var list = overlay.querySelector("#sheet-load-list");
    if (!list) { return; }
    var defaults = sheetLoadFresh();
    var days = answer.days || [];
    var writable = 0;
    days.forEach(function (item) {
      /* Совпавший день — не строка выбора: грузить в нём нечего, а подпись
         «у вас не отмечено» была бы ложью. Его учитывают счётчики ниже. */
      if (item.state === "same") { return; }
      if (sheetLoadWritable(item)) { writable += 1; }
      var on = Boolean(defaults[item.state]);
      state.order.push(item.day);
      state.selected[item.day] = on;
      var row = item.state === "unparsed"
        ? sheetLoadInfoRow(item)
        : sheetLoadSwitchRow(item, on);
      if (item.state !== "unparsed") {
        row.onclick = function () { sheetLoadToggle(item, row); };
      }
      list.appendChild(row);
    });

    var counts = answer.counts || {};
    /* «Отмечено в листе» — по непустым клеткам ответа: совпавшие дни уже в `days`,
       прибавлять `counts.same` значило бы посчитать их дважды (К2). */
    var inSheet = days.filter(function (item) {
      return item.sheet_state && item.sheet_state !== "empty";
    }).length;
    if (days.length) {
      sheetLoadText(overlay, "#sheet-load-note",
        "Отмечено в листе: " + sheetLoadDays(inSheet) + " из 7.");
    }
    if (counts.same) {
      sheetLoadText(overlay, "#sheet-load-same",
        "Совпало: " + counts.same + " " + plural(counts.same, "день", "дня", "дней") + " из 7.");
    }
    if (!writable && counts.same && counts.same === days.length) {
      sheetLoadNothing(list);
    }
    sheetLoadPrimary();
  }

  async function sheetLoadPreview(overlay, start) {
    var answer = await api("/webapp/availability/sheet/preview?start="
      + encodeURIComponent(start));
    if (!answer.body.ok) {
      sheetLoadFail(overlay, answer.body.detail
        || "Не удалось прочитать общий лист. Проверьте сеть и повторите.");
      return;
    }
    if (sheetLoadState && sheetLoadState.overlay === overlay) {
      sheetLoadRender(overlay, answer.body);
    }
  }

  function openSheetLoad() {
    if (!week) {
      SVOI.toast("Сначала дождитесь загрузки недели. Проверьте сеть и повторите.");
      return;
    }
    var overlay = SVOI.sheet({
      title: "Загрузить из общего листа",
      body: '<p class="ui-text" id="sheet-load-intro">Читаю общий лист…</p>'
        + '<p class="ui-small ui-muted" id="sheet-load-note"></p>'
        + '<div class="ui-list" id="sheet-load-list"></div>'
        + '<p class="ui-small ui-muted" id="sheet-load-same"></p>',
      actions: [
        { label: "Отмена" },
        { label: "Выберите дни", primary: true, keepOpen: true,
          onTap: function () { submitSheetLoad(); } },
      ],
    });
    var buttons = overlay.querySelectorAll(".ui-sheet__actions .ui-btn");
    sheetLoadState = {
      overlay: overlay,
      start: week.start,
      order: [],
      selected: {},
      primary: buttons.length ? buttons[buttons.length - 1] : null,
      busy: false,
    };
    sheetLoadPrimary();
    sheetLoadPreview(overlay, week.start);
  }

  async function submitSheetLoad() {
    var state = sheetLoadState;
    if (!state || state.busy || !state.primary) { return; }
    var days = state.order.filter(function (day) { return state.selected[day]; });
    if (!days.length) { return; }
    /* Снимок прежних отметок — до записи: «Вернуть» возвращает именно его. */
    var snapshot = week.days.filter(function (day) { return day.marked; }).map(toPayload);
    state.busy = true;
    state.primary.disabled = true;
    state.primary.textContent = "Загружаю…";
    var answer = await api("/webapp/availability/sheet/load", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start: state.start, days: days }),
    });
    state.busy = false;
    if (!answer.body.ok) {
      sheetLoadFail(state.overlay, answer.body.detail
        || "Не удалось загрузить дни. Проверьте сеть и повторите.");
      return;
    }
    SVOI.closeSheet();
    sheetLoadState = null;
    await load(week.start);
    var applied = (answer.body.days || []).length;
    SVOI.toast("Загружено: " + sheetLoadDays(applied), {
      action: "Вернуть", duration: 10000,
      onAction: function () { restoreSheetLoad(snapshot); },
    });
  }

  async function restoreSheetLoad(snapshot) {
    var failed = false;
    var lost = week.days.some(function (day) {
      return day.marked && !snapshot.some(function (item) { return item.day === day.day; });
    });
    if (lost) {
      var cleared = await api("/webapp/availability/clear", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ week: week.start }),
      });
      failed = !cleared.body.ok;
    }
    if (!failed && snapshot.length) {
      var answer = await api("/webapp/availability/week", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start: week.start, days: snapshot }),
      });
      failed = !answer.body.ok;
    }
    if (failed) {
      SVOI.toast("Не удалось вернуть прежние отметки. Проверьте сеть и повторите.");
      return;
    }
    await load(week.start);
    SVOI.toast("Вернул как было.");
  }

  $("load-from-sheet").onclick = openSheetLoad;
"""

__all__ = ["ROW_LOAD_FROM_SHEET", "_SCRIPT_WEEK_SHEET_LOAD"]
