"""Экран карточки события (docs/development/190 §3, дизайн 187 §3).

Ключ карточки — календарь и id события Google, а не `event_plan.id`: людские
события (446 из календаря) строки в базе не имеют вовсе. Одна карточка на все
случаи: тип по метке или «Без типа», пустое поле — без строки, причины отказа
(`human_event`, `live_calendar`, `cancelled`, `not_your_event`, `plan_missing`)
написаны словами, а не кодом.

«Перенести» — единственное действие первой версии: открывает лист с датой и
часами и зовёт существующий `POST /tools/events/move`. Кнопка появляется только
при `editable: true` — тому же решению `_can_move`, что проверяет перенос.
"""

from __future__ import annotations

from app.pages import layout

_BODY_EVENT_CARD = """
<div class="ui-small ui-muted" id="status" role="status">Открываю событие…</div>

<article class="ui-card" id="card" hidden>
  <span class="ui-badge ev-plashka" id="type">Без типа</span>
  <h1 class="ui-card__title" id="title"></h1>
  <div class="ui-list" id="rows">
    <div class="ui-row" id="row-date" hidden>
      <span class="ui-row__main"><span class="ui-row__title">Когда</span></span>
      <span class="ui-row__value" id="date"></span>
    </div>
    <div class="ui-row" id="row-time" hidden>
      <span class="ui-row__main"><span class="ui-row__title">Время</span></span>
      <span class="ui-row__value" id="time"></span>
    </div>
    <div class="ui-row" id="row-mine" hidden>
      <span class="ui-row__main"><span class="ui-row__title">Ты задействован</span></span>
      <span class="ui-row__value" id="mine"></span>
    </div>
    <div class="ui-row" id="row-place" hidden>
      <span class="ui-row__main"><span class="ui-row__title">Место</span></span>
      <span class="ui-row__value" id="place"></span>
    </div>
    <div class="ui-row" id="row-creator" hidden>
      <span class="ui-row__main"><span class="ui-row__title">Создал</span></span>
      <span class="ui-row__value" id="creator"></span>
    </div>
    <div class="ui-row" id="row-color" hidden>
      <span class="ui-row__main"><span class="ui-row__title">Цвет</span></span>
      <span class="ui-row__value" id="color"></span>
    </div>
  </div>
  <p class="ui-text" id="description" hidden></p>
</article>

<div class="ui-card" id="why" hidden>
  <span class="ui-small ui-muted" id="why-text"></span>
</div>

<button class="ui-btn ui-btn--primary ui-btn--block" id="move" type="button" hidden>Перенести</button>

<div class="ui-alert" id="error" hidden></div>
"""

_SCRIPT_EVENT_CARD = r"""<script>
(function () {
  "use strict";
  window.SVOI_BACK = "/webapp/home";
  var tg = window.Telegram && window.Telegram.WebApp;
  function boot() { if (tg) { tg.ready(); tg.expand(); } }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, {once: true});
  } else { boot(); }

  var $ = function (id) { return document.getElementById(id); };
  var params = new URLSearchParams(location.search);
  var calendar = params.get("calendar") || "read";
  var eventId = params.get("event_id") || "";
  var event = null;

  var REASONS = {
    human_event: "Событие создано в Google — правьте там.",
    live_calendar: "Событие в боевом календаре — правьте в Google.",
    cancelled: "Событие отменено.",
    not_your_event: "Переносит создатель события или администратор.",
    plan_missing: "У события метка бота, но строки в расписании нет — сообщите администратору."
  };

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function setRow(row, value, fill) {
    if (!value) { $(row).hidden = true; return; }
    $(row).hidden = false;
    if (fill) { fill.textContent = value; }
  }

  function draw(data) {
    event = data;
    $("type").textContent = data.kind_title || "Без типа";
    $("type").className = "ui-badge ev-plashka" +
      (data.color_id ? " ev-color-" + data.color_id : "");
    $("title").textContent = data.title || "Без названия";

    var when = data.date || "";
    if (data.weekday) { when = when ? when + ", " + data.weekday : data.weekday; }
    setRow("row-date", when, $("date"));

    var clock = "";
    if (!data.all_day && data.time_from) {
      clock = data.time_from + (data.time_to ? "–" + data.time_to : "");
    }
    setRow("row-time", clock, $("time"));
    // Личное время участия (V-134): пустое окно уже расширено интервалом
    // события на сервере, поэтому строка есть ровно у участника события.
    var mine = data.my_time;
    setRow("row-mine", mine ? mine.time_from + "–" + mine.time_to : "", $("mine"));
    setRow("row-place", data.location, $("place"));
    setRow("row-creator", data.creator ? data.creator.name : "",
           $("creator"));
    setRow("row-color", data.color_id ? "цвет " + data.color_id : "",
           $("color"));

    var text = data.description || "";
    $("description").hidden = !text;
    $("description").textContent = text;

    var reason = data.editable ? "" : (REASONS[data.not_editable_reason] || "");
    $("why").hidden = !reason;
    $("why-text").textContent = reason;
    $("move").hidden = !data.editable;
    $("card").hidden = false;
    $("status").hidden = true;
  }

  function moveSheet() {
    if (!event) { return; }
    var body = document.createElement("div");
    body.className = "ui-form";
    body.innerHTML =
      '<div class="ui-field"><span class="ui-field__label">Дата</span>' +
      '<input class="ui-input ui-datefield" id="m-date" value="' + (event.date || "") + '"></div>' +
      '<div class="ui-range">' +
      '<div class="ui-field"><span class="ui-field__label">с</span>' +
      '<input class="ui-input" id="m-from" inputmode="numeric" maxlength="5" value="' +
      (event.time_from || "") + '"></div>' +
      '<span class="ui-range__sep">—</span>' +
      '<div class="ui-field"><span class="ui-field__label">до</span>' +
      '<input class="ui-input" id="m-to" inputmode="numeric" maxlength="5" value="' +
      (event.time_to || "") + '"></div></div>' +
      '<span class="ui-field__error" id="m-error" hidden></span>';
    SVOI.sheet({
      title: "Перенести событие",
      body: body,
      actions: [
        {label: "Отмена"},
        {label: "Перенести", primary: true, onTap: function () { submitMove(); }}
      ]
    });
    window.__moveForm = body;
  }

  var MOVE_TEXTS = {
    slot_taken: "Зал занят в это время — выберите другое.",
    not_your_event: "Переносит создатель события или администратор.",
    event_cancelled: "Событие отменено — переносить нечего.",
    outside_working_hours: "Вне рабочих часов: с 08:00 до 23:00.",
    bad_interval: "Конец раньше начала."
  };

  function submitMove() {
    var form = window.__moveForm;
    if (!form) { return; }
    var date = form.querySelector("#m-date").value.trim();
    var from = form.querySelector("#m-from").value.trim();
    var to = form.querySelector("#m-to").value.trim();
    var fail = form.querySelector("#m-error");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{1,2}:\d{2}$/.test(from)
        || !/^\d{1,2}:\d{2}$/.test(to)) {
      fail.textContent = "Дата — ГГГГ-ММ-ДД, время — ЧЧ:ММ.";
      fail.hidden = false;
      return;
    }
    SVOI.api("/tools/events/move", {
      event_id: event.plan_id,
      date: date,
      from_hour: Number(from.split(":")[0]),
      to_hour: Number(to.split(":")[0]),
      allow_outside_hours: false
    }).then(function (res) {
      if (res.status === 200 && res.body && res.body.ok) {
        SVOI.closeSheet();
        if (res.body.calendar_error) {
          SVOI.toast("Время изменено в расписании; в календарь записать не удалось.");
        } else {
          SVOI.toast("Перенесено.");
        }
        load();
        return;
      }
      var code = (res.body && res.body.code) || "";
      var text = MOVE_TEXTS[code];
      if (!text && res.body && typeof res.body.detail === "string") {
        text = MOVE_TEXTS[res.body.detail] || "";
      }
      if (res.status === 502) {
        text = "Календарь недоступен — время изменено в расписании, повторите позже.";
      }
      fail.textContent = text || "Перенести не удалось. Попробуйте ещё раз.";
      fail.hidden = false;
    });
  }

  function problem(res) {
    if (res.status === 401) { return "Откройте приложение из бота — сессия не подтверждена."; }
    if (res.status === 403) { return "Календарь смотрит организатор и педагог."; }
    if (res.status === 404) { return "События нет в этом календаре."; }
    if (res.status === 502) { return "Календарь недоступен — повторите позже."; }
    if (res.status === 503) { return "Календарь не настроен: администратору нужно указать id календаря."; }
    return "Не удалось открыть событие. Попробуйте ещё раз.";
  }

  function load() {
    if (!eventId) {
      $("status").textContent = "Событие не выбрано: откройте его из календаря.";
      return;
    }
    SVOI.auth().then(function (denied) {
      if (denied) { $("status").textContent = denied; return; }
      SVOI.api("/webapp/events/card?calendar=" + encodeURIComponent(calendar) +
               "&event_id=" + encodeURIComponent(eventId)).then(function (res) {
        if (res.status === 200 && res.body && res.body.ok) {
          showError("");
          draw(res.body.event || {});
          return;
        }
        $("status").textContent = problem(res);
      });
    });
  }

  $("move").addEventListener("click", moveSheet);
  load();
})();
</script>"""

EVENT_CARD_PAGE = layout.page("Событие", _BODY_EVENT_CARD, back="/webapp/home",
                              scripts=_SCRIPT_EVENT_CARD)

