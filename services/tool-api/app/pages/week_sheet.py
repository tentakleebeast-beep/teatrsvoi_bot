"""Общая шапка скрипта недели: вход, подписи, чтение недели и разметка листа дня.

Лежит отдельно от `pages/week_marks.py`, потому что там лист дня и запись, а
здесь — то, что нужно и записи, и шаблону: `$`, `api`, `humanDate`, `describe`,
`render`, `load`, `shiftWeek` и разметка листа (`sheetBody`). Иначе модуль
перерастает порог ~400 строк, и разрез недели теряет смысл (N-1, R-9,
docs/reports/101).

Вход (`auth`) и токен — из оболочки (`static/webapp-shell.js`): своей копии
здесь нет.
"""

from __future__ import annotations

_SCRIPT_WEEK_SHEET = r"""

<script>
(function () {
  "use strict";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); if (tg.BackButton) tg.BackButton.hide(); }
  window.SVOI_BACK = "/webapp/week";

  var $ = function (id) { return document.getElementById(id); };
  var params = new URLSearchParams(location.search);
  var start = params.get("start") || "";
  var week = null;
  /* Запись недели, которая уже ушла на сервер. Переключение недели ждёт её:
     иначе новый экран читался бы до сохранения, и «несохранённое» выглядело бы
     потерянным (П-4). Держим именно запрос, а не весь `persist`: иначе `load`
     внутри сохранения ждал бы сам себя. */
  var pendingSave = null;

  var WEEKDAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
                "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  /* Чипы — заготовки интервала занятости. «Весь день» здесь означает рабочий
     день 08:00–23:00: 24:00 в поле времени не набирается, а «занят весь день»
     уже есть отдельным вариантом выше. */
  var CHIPS = [["После 18:00", "18:00", "23:00"],
               ["До 15:00", "08:00", "15:00"],
               ["Весь день", "08:00", "23:00"]];
  var MODES = [["free", "Свободен весь день"],
               ["busy", "Занят весь день"],
               ["hours", "Указать часы"]];

  function pad(value) { return value < 10 ? "0" + value : String(value); }

  /* Время пишем цифрами и всегда в 24 часах: «18:00». Нативное поле времени на
     английской локали показывало AM/PM, а шаг 30 минут всё равно наш — округляем сами. */
  function clock(value) {
    var text = String(value || "").replace(/\s+/g, "");
    if (!text) { return ""; }
    var match = /^(\d{1,2})(?::?(\d{2}))?$/.exec(text);
    if (!match) { return null; }
    var hour = Number(match[1]);
    var minute = match[2] ? Number(match[2]) : 0;
    if (hour > 23 || minute > 59) { return null; }
    minute = Math.round(minute / 30) * 30;
    if (minute === 60) { hour += 1; minute = 0; }
    if (hour > 23) { return null; }
    return pad(hour) + ":" + pad(minute);
  }

  function todayIso() {
    var now = new Date();
    return now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
  }

  function plural(count, one, few, many) {
    if (count % 10 === 1 && count % 100 !== 11) { return one; }
    if (count % 10 >= 2 && count % 10 <= 4 && !(count % 100 >= 12 && count % 100 <= 14)) {
      return few;
    }
    return many;
  }

  /* Вход и запрос — из оболочки (static/webapp-shell.js): своя копия была
     третьей и отличалась обработкой ошибок. */
  var auth = window.SVOI.auth;

  /* Токен, повтор после 401 и разбор ошибок — общие, из shell.js. */
  var api = window.SVOI.api;

  function humanDate(iso) {
    var parts = String(iso || "").split("-").map(Number);
    if (parts.length !== 3) { return iso || ""; }
    var date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
    return parts[2] + " " + MONTHS[parts[1] - 1] + ", " + ["воскресенье", "понедельник",
      "вторник", "среда", "четверг", "пятница", "суббота"][date.getUTCDay()];
  }

  function dayTitle(day) {
    var parts = String(day.day || "").split("-").map(Number);
    var date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
    return WEEKDAY_SHORT[(date.getUTCDay() + 6) % 7] + " " + parts[2];
  }

  /* Подпись под названием — только часы и комментарий, без повтора слова
     «занят»: оно уже стоит справа и передано цветом. */
  function hours(day) {
    if (!day.marked || day.status === "uncertain") { return ""; }
    if (!day.from) { return day.to ? "до " + day.to : "весь день"; }
    return day.to ? "с " + day.from + " до " + day.to : "с " + day.from;
  }
  function describe(day) {
    if (!day.marked) { return "не отмечено"; }
    if (day.status === "uncertain") { return "пока не понятно"; }
    var word = day.status === "free" ? "свободен" : "занят";
    if (week && week.female) { word = word === "занят" ? "занята" : "свободна"; }
    return word + " " + hours(day);
  }

  function stateWord(day) {
    if (!day.marked) { return "не отмечено"; }
    if (day.status === "uncertain") { return "пока не понятно"; }
    var female = week && week.female;
    return day.status === "free" ? (female ? "свободна" : "свободен")
                                 : (female ? "занята" : "занят");
  }

  function render() {
    $("week-title").textContent = week.title;
    var other = $("to-summary");
    if (other && week.start) {
      other.href = "/webapp/week/summary?start=" + encodeURIComponent(week.start);
    }
    var today = todayIso();
    var marked = 0;
    week.days.forEach(function (day, index) {
      if (day.marked) { marked += 1; }
      var row = document.querySelector('[data-day-slot="' + index + '"]');
      if (!row) { return; }
      row.querySelector(".ui-row__title").textContent = dayTitle(day);
      row.querySelector(".ui-day__state").textContent = stateWord(day);
      var meta = hours(day);
      if (day.note) { meta = meta ? meta + " · " + day.note : day.note; }
      row.querySelector(".ui-row__meta").textContent = meta;
      row.setAttribute("data-state",
        !day.marked ? "empty" : (day.status || "busy"));
      row.classList.toggle("ui-row--today", day.day === today);
      var flag = row.querySelector(".ui-day__today");
      if (flag) { flag.hidden = day.day !== today; }
    });
    $("progress").textContent = "Отмечено " + marked + " из 7 дней";
  }

  function toPayload(day) {
    return { day: day.day, status: day.status, from_time: day.from || null,
             to_time: day.to || null, note: day.note || null };
  }

  async function load(nextStart) {
    /* Сначала дожидаемся записи, которая ещё летит: отметки автосохраняются,
       и смена недели не должна показывать данные до сохранения (П-4). */
    if (pendingSave) {
      var pending = pendingSave;
      pendingSave = null;
      try { await pending; } catch (e) { /* ошибку уже показали в сохранении */ }
    }
    var query = nextStart ? "?start=" + encodeURIComponent(nextStart) : "";
    var answer = await api("/webapp/availability/week" + query);
    if (answer.status === 401) {
      $("status").textContent = "Откройте приложение из бота — сессия не подтверждена.";
      return false;
    }
    if (!answer.body.ok) {
      $("status").textContent = "Не удалось загрузить неделю. Проверьте сеть и повторите.";
      return false;
    }
    $("status").textContent = "";
    week = answer.body;
    start = week.start;
    render();
    return true;
  }

  function shiftWeek(iso, weeks) {
    /* Дату считаем в UTC: локальная полночь в UTC+4 — предыдущий день по UTC. */
    var parts = iso.split("-").map(Number);
    var base = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
    base.setUTCDate(base.getUTCDate() + 7 * weeks);
    return base.toISOString().slice(0, 10);
  }

  function modeRow(key, label) {
    return '<button type="button" class="ui-radio-row" data-mode="' + key + '">'
      + '<span class="ui-text">' + label + '</span>'
      + '<span class="ui-radio-row__mark"></span></button>';
  }

  function sheetBody() {
    var chips = CHIPS.map(function (chip) {
      return '<button type="button" class="ui-chip" data-from="' + chip[1]
        + '" data-to="' + chip[2] + '">' + chip[0] + '</button>';
    }).join("");
    return '<div style="display:flex;flex-direction:column;gap:8px" id="day-modes">'
      + MODES.map(function (mode) { return modeRow(mode[0], mode[1]); }).join("")
      + "</div>"
      + '<div class="ui-field" id="hours-block">'
      + '<span class="ui-field__label">Часы</span>'
      + '<div class="ui-segmented" id="hours-state" role="group" aria-label="Что делаете в эти часы">'
      + '<button class="ui-segmented__item" type="button" data-state="busy">Занят</button>'
      + '<button class="ui-segmented__item" type="button" data-state="free">Свободен</button>'
      + "</div>"
      + '<div class="ui-range">'
      + '<div class="ui-field" style="min-width:0">'
      + '<label class="ui-field__label" for="day-from">с</label>'
      + '<input class="ui-input ui-num" id="day-from" type="text" inputmode="numeric" '
      + 'maxlength="5" autocomplete="off"></div>'
      + '<div class="ui-field" style="min-width:0">'
      + '<label class="ui-field__label" for="day-to">до</label>'
      + '<input class="ui-input ui-num" id="day-to" type="text" inputmode="numeric" '
      + 'maxlength="5" autocomplete="off"></div>'
      + "</div>"
      + '<div class="ui-chips">' + chips + "</div>"
      + '<span class="ui-field__hint">Пустое поле — край дня: 08:00 или 24:00.</span>'
      + '<span class="ui-field__error" id="day-error" hidden></span>'
      + "</div>"
      + '<div class="ui-field">'
      + '<label class="ui-field__label" for="day-note">Комментарий, если нужен</label>'
      + '<textarea class="ui-textarea" id="day-note" maxlength="120" '
      + 'style="min-height:88px"></textarea>'
      + "</div>";
  }

"""

__all__ = ["_SCRIPT_WEEK_SHEET"]
