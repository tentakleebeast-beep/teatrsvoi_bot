"""Сетка месяца «люди × дни» — только чтение (FR-H9, З9, docs/development/48 §3).

Экран нужен организатору перед репетицией: увидеть, кто занят и кто не отметился,
не открывая Google-таблицу руками. Источник — тот же лист «Расписание студийцы»
(`GET /webapp/month-grid`), база — вторая сторона: где они расходятся, экран
показывает списком и ничего не правит (П-3). Правка дня живёт в листе дня на
экране «Время» (З1).

Почему месяц показан неделями, а не одной простынёй 30 × 16: на 390 px тридцать
колонок со словами «занят / свободен» не помещаются, а горизонтальный скролл
запрещён дизайн-системой (`docs/development/91`, тест «нет горизонтального
скролла»). Поэтому месяц — это период, а столбцы — дни одной недели; `‹`/`›`
листают недели внутри месяца. Сетка не таблица: клетки — `div`, у них нет
обработчиков, нажатие ничего не делает (П-3).
"""

from __future__ import annotations

from app.pages import layout
from app.pages.week_shared import week_nav

_BODY_MONTH = (
    week_nav()
    + '<div class="ui-section__head" style="padding:0">'
    '<span class="ui-small ui-muted" id="month-head">сетка месяца</span>'
    '<span class="ui-small ui-muted" id="week-count"></span></div>'
    + '<div class="ui-month" id="grid" aria-live="polite"></div>'
    + '<div class="ui-small ui-muted" id="legend">'
    "занят · свободен · не уверен · прочерк — не отмечено</div>"
    + '<div class="ui-small ui-muted" id="status"></div>'
    '<div class="ui-section" id="diffs" hidden>'
    '<div class="ui-section__head"><h2 class="ui-section__title">Расхождения с базой</h2>'
    '<span class="ui-section__meta" id="diffs-count"></span></div>'
    '<div class="ui-list" id="diffs-list"></div>'
    '<div class="ui-small ui-muted">Экран ничего не меняет: правку листа переносит '
    "«Сверка листов».</div>"
    "</div>"
)

_SCRIPT_MONTH = r"""
<script>
(function () {
  "use strict";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); if (tg.BackButton) tg.BackButton.hide(); }
  window.SVOI_BACK = "/webapp/week/summary";

  var $ = function (id) { return document.getElementById(id); };
  var params = new URLSearchParams(location.search);
  var month = params.get("month") || "";

  var WEEKDAY = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
                "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  var MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь",
                    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

  var auth = window.SVOI.auth;
  var api = window.SVOI.api;

  var view = { title: "", words: {}, days: [], people: [], cells: {}, diffs: [] };
  var weeks = [];
  var at = 0;

  function pad(value) { return value < 10 ? "0" + value : String(value); }

  function toDate(value) {
    var parts = String(value || "").split("-").map(Number);
    return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  }

  function iso(date) { return date.toISOString().slice(0, 10); }

  function dayNumber(value) { return Number(value.slice(8, 10)); }

  function monthLabel(monthKey) {
    var parts = String(monthKey || "").split("-").map(Number);
    if (parts.length < 2) { return ""; }
    return MONTHS_NOM[parts[1] - 1] + " " + parts[0];
  }

  /* Недели месяца, выровненные по понедельнику: строки сетки — недели,
     столбцы — дни недели. Дни чужого месяца остаются пустыми. */
  function splitWeeks(days) {
    if (!days.length) { return []; }
    var first = toDate(days[0]);
    var last = toDate(days[days.length - 1]);
    var start = new Date(first.getTime());
    start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));
    var out = [];
    for (var cursor = start; cursor <= last; cursor.setUTCDate(cursor.getUTCDate() + 7)) {
      var line = [];
      for (var offset = 0; offset < 7; offset += 1) {
        var day = new Date(cursor.getTime());
        day.setUTCDate(day.getUTCDate() + offset);
        line.push(iso(day));
      }
      out.push(line);
    }
    return out;
  }

  function span(className, text) {
    var node = document.createElement("span");
    node.className = className;
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function cell(person, day) {
    var monthDays = view.days.indexOf(day) !== -1;
    var node = document.createElement("div");
    node.className = "ui-mcell";
    node.setAttribute("data-day", day);
    node.setAttribute("data-person", String(person.id));
    if (!monthDays) {
      node.classList.add("ui-mcell--out");
      return node;
    }
    var mark = (view.cells[person.id] || {})[day] || {};
    var state = mark.state || "empty";
    node.setAttribute("data-state", state);
    /* «Не отмечено» в клетке — прочерк, слова — в легенде: одиннадцать букв
       в узкую колонку не влезают, а обрезанное слово хуже прочерка (docs/91). */
    node.textContent = state === "empty" ? "—" : (mark.word || view.words[state] || "—");
    var label = person.name + ", " + dayNumber(day) + ": "
      + (mark.word || view.words[state] || "не отмечено");
    if (mark.diff) {
      /* Расхождение видно и в клетке, и списком ниже: в базе — другое слово. */
      node.setAttribute("data-diff", "1");
      label += ", в базе: " + (mark.base_word || "не отмечено");
    }
    node.setAttribute("aria-label", label);
    node.title = label;
    return node;
  }

  function renderWeek() {
    var grid = $("grid");
    grid.textContent = "";
    var line = weeks[at] || [];
    var first = line[0] || "";
    var last = line[6] || "";
    $("week-title").textContent = first && last
      ? dayNumber(first) + " — " + dayNumber(last) + " " + MONTHS[toDate(last).getUTCMonth()]
      : "…";

    var head = document.createElement("div");
    head.className = "ui-month__row ui-month__row--head";
    head.appendChild(span("ui-month__name", ""));
    line.forEach(function (day) {
      var cellNode = span("ui-month__day",
        WEEKDAY[(toDate(day).getUTCDay() + 6) % 7] + " " + dayNumber(day));
      head.appendChild(cellNode);
    });
    grid.appendChild(head);

    if (!view.people.length) {
      var empty = document.createElement("div");
      empty.className = "ui-empty";
      empty.innerHTML = '<div class="ui-empty__title">Пока никого</div>'
        + '<div class="ui-empty__text">В студии нет активных участников.</div>';
      grid.appendChild(empty);
      return;
    }

    view.people.forEach(function (person) {
      var row = document.createElement("div");
      row.className = "ui-month__row";
      row.setAttribute("data-person", String(person.id));
      var name = document.createElement("span");
      name.className = "ui-month__name";
      var title = span("ui-month__who", person.name);
      name.appendChild(title);
      if (person.sheet) {
        var note = span("ui-month__note", person.sheet);
        name.appendChild(note);
      }
      row.appendChild(name);
      line.forEach(function (day) { row.appendChild(cell(person, day)); });
      grid.appendChild(row);
    });

    $("prev-week").disabled = at <= 0;
    $("next-week").disabled = at >= weeks.length - 1;
    $("week-count").textContent = view.people.length
      ? "людей: " + view.people.length : "";
  }

  function renderDiffs() {
    var host = $("diffs-list");
    host.textContent = "";
    if (!view.diffs.length) {
      $("diffs").hidden = true;
      return;
    }
    view.diffs.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "ui-row";
      var main = span("ui-row__main");
      main.appendChild(span("ui-row__title", dayNumber(item.day) + " " + MONTHS[toDate(item.day).getUTCMonth()]));
      main.appendChild(span("ui-row__meta", item.text || ""));
      row.appendChild(main);
      host.appendChild(row);
    });
    $("diffs-count").textContent = "всего " + view.diffs.length;
    $("diffs").hidden = false;
  }

  async function load() {
    var answer = await api("/webapp/month-grid" + (month ? "?month=" + month : ""));
    if (answer.status === 401) {
      $("status").textContent = "Откройте приложение из бота — сессия не подтверждена.";
      return false;
    }
    if (answer.status === 403) {
      $("status").textContent = "Сетка месяца — у организатора.";
      return false;
    }
    if (!answer.body.ok) {
      $("status").textContent = "Не удалось загрузить сетку. Проверьте сеть и повторите.";
      return false;
    }
    view.words = answer.body.words || {};
    view.days = answer.body.days || [];
    view.people = answer.body.people || [];
    view.cells = answer.body.cells || {};
    view.diffs = answer.body.diffs || [];
    weeks = splitWeeks(view.days);
    at = 0;
    var now = new Date();
    var today = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
    weeks.forEach(function (line, index) {
      if (line.indexOf(today) !== -1) { at = index; }
    });
    $("month-head").textContent = monthLabel(answer.body.month || month)
      + " · месячная сетка, только чтение"
      + (answer.body.note ? " · " + answer.body.note : "");
    $("status").textContent = "";
    renderWeek();
    renderDiffs();
    return true;
  }

  $("prev-week").onclick = function () {
    if (at > 0) { at -= 1; renderWeek(); }
  };
  $("next-week").onclick = function () {
    if (at < weeks.length - 1) { at += 1; renderWeek(); }
  };

  (async function boot() {
    var denied = await auth();
    if (denied) {
      $("status").textContent = denied;
      return;
    }
    await load();
  })();
})();
</script>
"""

MONTH_PAGE = layout.page("Сетка месяца", _BODY_MONTH, back="/webapp/week/summary",
                         scripts=_SCRIPT_MONTH)

__all__ = ["MONTH_PAGE"]
