"""Экран «Сверка календаря»: что изменили в Google руками (docs/development/190 §5).

Отдельный модуль от карточки события: экран с своим списком и отчётом, и порог
~400 строк на модуль страницы держится (`tests/unit/test_webapp_pages_split.py`).

Экран ничего не пишет: `POST /tools/google/refresh` всегда уходит с `dry_run: true`
(§5.2), поэтому найденный перенос показывается честной строкой «черновик не удалён —
экран ничего не пишет», а не притворяется выполненным.

Право `schedule.read.all`: без него вместо кнопки стоит фраза, кто ведёт сверку
(образец `misc_reconcile.py`), а отказ 403 на самом вызове ведёт к тому же тексту.
"""

from __future__ import annotations

from app.pages import layout

_BODY_EVENTS_RECONCILE = """
<p class="ui-small ui-muted" id="intro">Сверяет календарь с расписанием: что изменили
в Google руками. Ничего не пишет.</p>

<div class="ui-small ui-muted" id="fresh" role="status">Смотрю, когда календарь читали…</div>
<div class="ui-alert" id="stale" hidden>Данные могли устареть.</div>

<div class="ui-card" id="denied" hidden>
  <span class="ui-card__label">Сверку календаря ведёт организатор</span>
  <span class="ui-small ui-muted">Расхождения календаря и расписания закрывает он:
    у вашей роли сверки нет.</span>
</div>

<div class="ui-range" id="month-nav">
  <button class="ui-btn ui-btn--ghost" id="prev" type="button" aria-label="Предыдущий месяц">&#8249;</button>
  <span class="ui-range__sep" id="month">месяц</span>
  <button class="ui-btn ui-btn--ghost" id="next" type="button" aria-label="Следующий месяц">&#8250;</button>
</div>

<button class="ui-btn ui-btn--primary ui-btn--block" id="check" type="button">Сверить</button>

<section class="ui-section" id="report" hidden>
  <div class="ui-card"><span class="ui-card__label">Изменено в Google</span>
    <span class="ui-amount" id="c-changed">0</span>
    <span class="ui-small ui-muted" id="changed-note">часы, название или место разошлись</span></div>
  <div class="ui-card"><span class="ui-card__label">Перенесено в боевой</span>
    <span class="ui-amount" id="c-moved">0</span>
    <span class="ui-small ui-muted" id="moved-note">черновик не удалён — экран ничего не пишет</span></div>
  <div class="ui-card"><span class="ui-card__label">Цвет не по типу</span>
    <span class="ui-amount" id="c-color">0</span>
    <span class="ui-small ui-muted" id="color-note">настройка типа главнее цвета события</span></div>
  <div class="ui-card"><span class="ui-card__label">Метка не по типу</span>
    <span class="ui-amount" id="c-label">0</span>
    <span class="ui-small ui-muted" id="label-note">в заголовке метка другого типа</span></div>
  <div class="ui-card"><span class="ui-card__label">Без типа</span>
    <span class="ui-amount" id="c-untyped">0</span>
    <span class="ui-small ui-muted" id="untyped-note">ни метки, ни типа бота</span></div>
  <div class="ui-card" id="error-row" hidden><span class="ui-card__label">Не прочитано</span>
    <span class="ui-small" id="c-error"></span></div>
</section>

<section class="ui-section" id="events" hidden>
  <div class="ui-section__head"><h2 class="ui-section__title">События окна</h2></div>
  <div class="ui-small ui-muted" id="shown"></div>
  <div class="ui-list" id="event-list"></div>
  <button class="ui-btn ui-btn--block" id="more" type="button" hidden>Показать ещё</button>
</section>

<div class="ui-alert" id="error" hidden></div>
"""

_SCRIPT_EVENTS_RECONCILE = r"""<script>
(function () {
  "use strict";
  window.SVOI_BACK = "/webapp/more";
  var tg = window.Telegram && window.Telegram.WebApp;
  function boot() { if (tg) { tg.ready(); tg.expand(); } }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, {once: true});
  } else { boot(); }

  var $ = function (id) { return document.getElementById(id); };
  var MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
                "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
  var today = new Date();
  var month = { year: today.getFullYear(), month: today.getMonth() };
  var pageToken = null;

  function pad(value) { return (value < 10 ? "0" : "") + value; }

  function window_() {
    var first = new Date(month.year, month.month, 1);
    var last = new Date(month.year, month.month + 1, 0);
    return { from: first.getFullYear() + "-" + pad(first.getMonth() + 1) + "-01",
             to: last.getFullYear() + "-" + pad(last.getMonth() + 1) + "-" + pad(last.getDate()) };
  }

  function drawMonth() {
    $("month").textContent = MONTHS[month.month] + " " + month.year;
  }

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function deny() {
    $("intro").hidden = true;
    $("check").hidden = true;
    $("month-nav").hidden = true;
    $("report").hidden = true;
    $("events").hidden = true;
    $("denied").hidden = false;
  }

  function applyCaps(body) {
    if (!body || body.ok !== true || !body.screens) { return; }
    if (body.screens["/webapp/events/reconcile"] === false) { deny(); }
  }

  function fmtTime(value) {
    if (!value) { return ""; }
    var date = new Date(value);
    if (isNaN(date.getTime())) { return ""; }
    return pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  function drawFresh(sources) {
    var entry = (sources || []).filter(function (item) {
      return item.key === "calendar.read";
    })[0] || {};
    if (entry.error) {
      $("fresh").textContent = "Календарь не читается: " + entry.error;
      $("stale").hidden = true;
      return;
    }
    if (!entry.as_of) {
      $("fresh").textContent = "Календарь ещё не читали.";
      $("stale").hidden = true;
      return;
    }
    $("fresh").textContent = "Данные от " + fmtTime(entry.as_of) + ".";
    $("stale").hidden = !entry.stale;
  }

  function drawReport(report) {
    var counts = report.counts || {};
    $("c-changed").textContent = String(counts.changed_in_google || 0);
    $("c-moved").textContent = String(counts.moved_to_live || 0);
    $("c-color").textContent = String(counts.color_mismatch || 0);
    $("c-label").textContent = String(counts.label_mismatch || 0);
    $("c-untyped").textContent = String(counts.untyped || 0);
    $("report").hidden = false;

    var orphan = counts.orphan_marker || 0;
    $("changed-note").textContent = orphan
      ? "с меткой бота, но строки в расписании нет — " + orphan
      : "часы, название или место разошлись";

    var failure = report.error;
    $("error-row").hidden = !failure;
    $("c-error").textContent = failure
      ? "Календарь недоступен — повторите позже." : "";

    var rows = report.changed_in_google || [];
    if (!failure && rows.length) {
      var first = rows[0];
      $("changed-note").textContent = "в Google: " + (first.start_google || "—") +
        ", у нас: " + (first.start_db || "—");
    }
    var color = (report.color_mismatch || [])[0];
    if (color) {
      $("color-note").textContent = "цвет " + (color.color_id || "не задан") +
        ", у типа — " + color.expected_color_id;
    }
    var label = (report.label_mismatch || [])[0];
    if (label) {
      $("label-note").textContent = "в заголовке [" + (label.letter || "—") + "], тип — " +
        (label.kind || "—");
    }
    $("untyped-note").textContent = "событий без типа: " +
      (report.untyped ? report.untyped.total : 0) + " (из них людских — " +
      (report.untyped ? report.untyped.human : 0) + ")";
  }

  function drawEvents(answer, append) {
    var items = answer.items || [];
    var list = $("event-list");
    if (!append) { list.innerHTML = ""; }
    items.forEach(function (item) {
      var row = document.createElement("a");
      row.className = "ui-row";
      row.href = "/webapp/event?calendar=read&event_id=" + encodeURIComponent(item.event_id);
      var main = document.createElement("span");
      main.className = "ui-row__main";
      var title = document.createElement("span");
      title.className = "ui-row__title";
      title.textContent = item.title || "Без названия";
      var meta = document.createElement("span");
      meta.className = "ui-row__meta";
      meta.textContent = (item.start || "").slice(0, 16).replace("T", " ") +
        (item.letter ? " · [" + item.letter + "]" : " · без типа");
      main.appendChild(title);
      main.appendChild(meta);
      row.appendChild(main);
      list.appendChild(row);
    });
    var shown = list.querySelectorAll(".ui-row").length;
    $("shown").textContent = "показано " + shown;
    $("events").hidden = false;
    pageToken = answer.next_page_token || null;
    $("more").hidden = !pageToken;
  }

  function loadEvents(append) {
    var win = window_();
    var url = "/webapp/events/list?calendar=read&from=" + win.from + "&to=" + win.to;
    if (append && pageToken) { url += "&page_token=" + encodeURIComponent(pageToken); }
    return SVOI.api(url).then(function (res) {
      if (res.status === 403) { deny(); return; }
      if (res.status === 200 && res.body && res.body.ok) { drawEvents(res.body, append); }
    });
  }

  function refreshFreshness() {
    return SVOI.api("/tools/google/freshness").then(function (res) {
      if (res.status === 403) { deny(); return; }
      if (res.status === 200 && res.body && res.body.ok) { drawFresh(res.body.sources); }
    });
  }

  function check() {
    var button = $("check");
    if (button.disabled) { return; }
    button.disabled = true;
    button.textContent = "Сверяю…";
    showError("");
    var win = window_();
    SVOI.api("/tools/google/refresh", {
      sources: ["calendar.read"], dry_run: true, window: win
    }).then(function (res) {
      button.disabled = false;
      button.textContent = "Сверить";
      if (res.status === 403) { deny(); return; }
      if (res.status === 502 || res.status === 503) {
        showError("Календарь недоступен — повторите позже.");
        return;
      }
      if (res.status !== 200 || !res.body || !res.body.ok) {
        showError("Сверка не прошла. Попробуйте ещё раз через минуту.");
        return;
      }
      var block = (res.body.sources || {})["calendar.read"] || {};
      drawReport(block);
      refreshFreshness();
      loadEvents(false);
    });
  }

  $("check").addEventListener("click", check);
  $("prev").addEventListener("click", function () {
    month.month -= 1;
    if (month.month < 0) { month.month = 11; month.year -= 1; }
    drawMonth();
    pageToken = null;
    loadEvents(false);
  });
  $("next").addEventListener("click", function () {
    month.month += 1;
    if (month.month > 11) { month.month = 0; month.year += 1; }
    drawMonth();
    pageToken = null;
    loadEvents(false);
  });
  $("more").addEventListener("click", function () { loadEvents(true); });

  drawMonth();
  SVOI.auth().then(function (denied) {
    if (denied) { $("fresh").textContent = denied; return; }
    SVOI.api("/webapp/nav").then(function (answer) { applyCaps(answer.body); },
                                 function () { /* nav не ответил: решает сервер */ });
    refreshFreshness();
    loadEvents(false);
  });
})();
</script>"""

EVENTS_RECONCILE_PAGE = layout.page("Сверка календаря", _BODY_EVENTS_RECONCILE,
                                    back="/webapp/more",
                                    scripts=_SCRIPT_EVENTS_RECONCILE)
