"""Сводка занятости: те же семь строк, но с числами «свободны N из M».

Отдельный экран (`/webapp/week/summary`) и отдельный скрипт. Общие строка дня
и переключатель недель — в `pages/week_shared.py`, чтобы экраны не зависели
друг от друга.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.week_shared import _DAYS, _WEEK_NAV

_BODY_SUMMARY = (
    _WEEK_NAV
    + '<div class="ui-list" id="day-list">'
    + _DAYS
    + "</div>"
    + '<div class="ui-small ui-muted" id="status"></div>'
    + '<a class="ui-btn ui-btn--primary ui-btn--block" id="to-slots" href="/webapp/slots">'
    "Найти окно для события</a>"
    # Месяц — отдельное чтение (П-3, З9): сводка отвечает «кто свободен на неделе»,
    # сетка месяца — «что в таблице и где она расходится с базой».
    + '<a class="ui-row" id="to-month" href="/webapp/week/month">'
    '<span class="ui-row__main">'
    '<span class="ui-row__title">Сетка месяца</span>'
    '<span class="ui-row__meta">люди и дни из Google-таблицы, только чтение</span></span>'
    '<span class="ui-row__chevron">&#8250;</span></a>'
)

_SCRIPT_SUMMARY = r"""
<script>
(function () {
  "use strict";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); if (tg.BackButton) tg.BackButton.hide(); }
  window.SVOI_BACK = "/webapp/week";

  var $ = function (id) { return document.getElementById(id); };
  var params = new URLSearchParams(location.search);
  var start = params.get("start") || "";

  var WEEKDAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  var WEEKDAY_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"];
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
                "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  var WORDS = { free: "свободен", busy: "занят", uncertain: "не уверен" };

  function pad(value) { return value < 10 ? "0" + value : String(value); }

  function todayIso() {
    var now = new Date();
    return now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
  }

  function toDate(iso) {
    var parts = String(iso || "").split("-").map(Number);
    return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  }

  function iso(date) { return date.toISOString().slice(0, 10); }

  function weekStart(value) {
    var date = toDate(value || todayIso());
    date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
    return date;
  }

  function humanDate(day) {
    var date = toDate(day);
    return WEEKDAY_FULL[(date.getUTCDay() + 6) % 7] + ", " + date.getUTCDate() + " " + MONTHS[date.getUTCMonth()];
  }

  /* Вход и запрос — из оболочки (static/webapp-shell.js): своя копия была
     третьей и отличалась обработкой ошибок. */
  var auth = window.SVOI.auth;

  var api = window.SVOI.api;

  function names(list) {
    var head = list.slice(0, 3).join(", ");
    return list.length > 3 ? head + " и ещё " + (list.length - 3) : head;
  }

  var view = { first: null, days: [], people: [], marks: {} };

  function dayCount(day) {
    var total = view.people.length;
    if (!total) { return "людей нет"; }
    var free = 0;
    var marked = 0;
    view.people.forEach(function (person) {
      var mark = (view.marks[person.id] || {})[day];
      if (!mark) { return; }
      marked += 1;
      if (mark.state === "free") { free += 1; }
    });
    if (!marked) { return "никто не отметился"; }
    return "свободны " + free + " из " + total;
  }

  /* Подпись дня — одна строка: числа справа, имена — в подписи под названием,
     которая обрезается, а не переносится (V-028/в, 390×844). */
  function dayMissing(day) {
    var unknown = [];
    view.people.forEach(function (person) {
      if (!(view.marks[person.id] || {})[day]) { unknown.push(person.name); }
    });
    if (!view.people.length) { return ""; }
    if (!unknown.length) { return "отметили все"; }
    return "не отметились: " + names(unknown);
  }

  function covers(day) {
    var total = view.people.length;
    if (!total) { return "empty"; }
    var free = 0;
    var marked = 0;
    view.people.forEach(function (person) {
      var mark = (view.marks[person.id] || {})[day];
      if (!mark) { return; }
      marked += 1;
      if (mark.state === "free") { free += 1; }
    });
    if (!marked) { return "empty"; }
    if (free === total) { return "free"; }
    if (free === 0) { return "busy"; }
    return "mixed";
  }

  function render() {
    var first = view.first;
    var last = new Date(first.getTime());
    last.setUTCDate(last.getUTCDate() + 6);
    $("week-title").textContent = first.getUTCDate() + " " + MONTHS[first.getUTCMonth()]
      + " — " + last.getUTCDate() + " " + MONTHS[last.getUTCMonth()];

    var today = todayIso();
    view.days.forEach(function (day, index) {
      var row = document.querySelector('[data-day-slot="' + index + '"]');
      if (!row) { return; }
      var date = toDate(day);
      row.querySelector(".ui-row__title").textContent =
        WEEKDAY_SHORT[(date.getUTCDay() + 6) % 7] + " " + date.getUTCDate();
      row.querySelector(".ui-day__state").textContent = dayCount(day);
      row.querySelector(".ui-row__meta").textContent = dayMissing(day);
      row.setAttribute("data-state", covers(day));
      row.classList.toggle("ui-row--today", day === today);
      var flag = row.querySelector(".ui-day__today");
      if (flag) { flag.hidden = day !== today; }
    });
  }

  function openDay(day) {
    var rows = [];
    view.people.forEach(function (person) {
      var mark = (view.marks[person.id] || {})[day] || {};
      rows.push([person.name, WORDS[mark.state] || "не отмечено"]);
    });
    var overlay = SVOI.sheet({
      title: "Занятость: " + humanDate(day),
      body: '<div class="ui-list" id="people-list"></div>',
      actions: [{ label: "Закрыть", primary: true }],
    });
    var list = overlay.querySelector("#people-list");
    if (!rows.length) {
      var empty = document.createElement("div");
      empty.className = "ui-empty";
      empty.innerHTML = '<div class="ui-empty__title">Пока никого</div>'
        + '<div class="ui-empty__text">В студии нет активных участников.</div>';
      list.appendChild(empty);
      return;
    }
    rows.forEach(function (pair) {
      var row = document.createElement("div");
      row.className = "ui-row";
      var main = document.createElement("span");
      main.className = "ui-row__main";
      var title = document.createElement("span");
      title.className = "ui-row__title";
      title.textContent = pair[0];
      main.appendChild(title);
      row.appendChild(main);
      var value = document.createElement("span");
      value.className = "ui-row__value";
      value.textContent = pair[1];
      row.appendChild(value);
      list.appendChild(row);
    });
  }

  async function load() {
    var first = weekStart(start);
    var last = new Date(first.getTime());
    last.setUTCDate(last.getUTCDate() + 6);
    var months = [iso(first).slice(0, 7)];
    if (iso(last).slice(0, 7) !== months[0]) { months.push(iso(last).slice(0, 7)); }

    var people = [];
    var marks = {};
    for (var i = 0; i < months.length; i += 1) {
      var answer = await api("/webapp/grid?month=" + months[i]);
      if (answer.status === 401) {
        $("status").textContent = "Откройте приложение из бота — сессия не подтверждена.";
        return false;
      }
      /* 403 — это не сеть: `GET /webapp/grid` закрыт правом `schedule.read.all`
         (FR-H9). Участник может прийти сюда по старой ссылке или из закладки,
         поэтому экран говорит, у кого сводка есть, а не «проверьте сеть».
         Карта t_70a6d8e5, docs/architecture/25 §9.2. */
      if (answer.status === 403) {
        $("status").textContent = "Сводка занятости — у организатора.";
        return false;
      }
      if (!answer.body.ok) {
        $("status").textContent = "Не удалось загрузить сводку. Проверьте сеть и повторите.";
        return false;
      }
      people = answer.body.people || people;
      var part = answer.body.marks || {};
      Object.keys(part).forEach(function (pid) {
        var merged = marks[pid] || {};
        Object.keys(part[pid]).forEach(function (day) { merged[day] = part[pid][day]; });
        marks[pid] = merged;
      });
    }

    view.first = first;
    view.people = people;
    view.marks = marks;
    view.days = [];
    for (var offset = 0; offset < 7; offset += 1) {
      var day = new Date(first.getTime());
      day.setUTCDate(day.getUTCDate() + offset);
      view.days.push(iso(day));
    }
    $("status").textContent = "";
    /* «Найти окно» ведёт в поиск с датами той недели, которую человек смотрит:
       вход не должен заставлять заново выбирать период (docs/architecture/40 §5). */
    $("to-slots").href = "/webapp/slots?from=" + view.days[0] + "&to=" + view.days[6];
    render();
    return true;
  }

  $("prev-week").onclick = function () {
    var date = weekStart(start);
    date.setUTCDate(date.getUTCDate() - 7);
    location.href = "/webapp/week/summary?start=" + iso(date);
  };
  $("next-week").onclick = function () {
    var date = weekStart(start);
    date.setUTCDate(date.getUTCDate() + 7);
    location.href = "/webapp/week/summary?start=" + iso(date);
  };

  Array.prototype.forEach.call(document.querySelectorAll("[data-day-slot]"), function (row) {
    row.onclick = function () {
      var day = view.days[Number(row.getAttribute("data-day-slot"))];
      if (day) { openDay(day); }
    };
  });

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

SUMMARY_PAGE = layout.page("Сводка занятости", _BODY_SUMMARY, tab="week", scripts=_SCRIPT_SUMMARY)

__all__ = ["SUMMARY_PAGE"]
