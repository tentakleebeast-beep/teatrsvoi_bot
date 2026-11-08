"""Главная: загрузочное состояние, отрисовка и состояния ответа (docs/development/148).

Вынесено из `home_loading`-кода страницы (`home.py`): модуль страницы не должен
превышать 400 строк (`tests/unit/test_webapp_pages_split.py`), а загрузочный экран
со всеми состояниями занимает больше половины файла. `home.py` собирает страницу
через каркас и держит вторую страницу — «Ещё».

Правила, которые держит разметка:

* «СВОИ» — постоянный знак `h1#logo`; имя и роль — отдельные строки, на их месте
  скелет (высота полосы равна высоте будущей строки, поэтому подмена не двигает
  соседей);
* карточка недели настоящая с первого кадра, включая рабочую кнопку;
* готовое содержимое подставляется одной операцией: `#home-loading` заменяется
  корнем `.ui-home`;
* ошибка ответа — `.ui-alert` на месте скелета; при 5xx и обрыве страница молчит,
  сообщение даёт баннер оболочки (ответ владельца на V-047, docs/reports/74).
"""

from __future__ import annotations

BODY = """
<div id="home-loading" class="ui-home">
  <h1 class="ui-logo" id="logo">СВОИ</h1>
  <h2 class="ui-h2" id="name"><span class="ui-skeleton ui-skeleton--line-title"
    style="width:60%" aria-hidden="true"></span></h2>
  <div class="ui-small ui-muted" id="role"><span class="ui-skeleton ui-skeleton--line-text"
    style="width:35%" aria-hidden="true"></span></div>
  <div class="ui-card" id="week">
    <div class="ui-section__head" style="padding:0">
      <h2 class="ui-section__title">Занятость на неделе</h2>
      <span class="ui-section__meta" id="week-count"></span>
    </div>
    <span class="ui-small ui-muted" id="week-meta"><span
      class="ui-skeleton ui-skeleton--line-text" style="width:40%" aria-hidden="true"></span></span>
    <a class="ui-btn ui-btn--primary ui-btn--block" href="/webapp/week">Отметить занятость</a>
  </div>
</div>

<a class="ui-btn ui-btn--block" id="quick-tx" href="/webapp/tx" hidden
   data-svoi-cap="budget.write">Записать операцию</a>

<p class="ui-sr-only" role="status" aria-live="polite">Загружаем данные</p>
"""

SCRIPT = """
<script>
(function () {
  "use strict";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); if (tg.BackButton) tg.BackButton.hide(); }
  var $ = function (id) { return document.getElementById(id); };
  var main = document.querySelector("main");
  var statusNode = main ? main.querySelector('[role="status"]') : null;
  var loading = $("home-loading");
  // Копия загрузочного состояния: «Повторить» возвращает экран в него, а не
  // перезагружает страницу (docs/development/148 §3).
  var loadingTemplate = loading ? loading.cloneNode(true) : null;

  function say(text) {
    if (!statusNode) { return; }
    // Меняем значение текстового узла, а не пересобираем разметку: объявление для
    // скринридера не должно выглядеть как вставка нового блока.
    if (statusNode.firstChild) { statusNode.firstChild.nodeValue = text; }
    else { statusNode.appendChild(document.createTextNode(text)); }
  }

  function busy(value) {
    if (main) { main.setAttribute("aria-busy", value ? "true" : "false"); }
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function root() {
    var node = el("div", "ui-home ui-fade-in");
    var logo = el("h1", "ui-logo", "СВОИ");
    logo.id = "logo";
    node.appendChild(logo);
    return node;
  }

  function attentionRow(item) {
    var row = document.createElement("a");
    row.className = "ui-row ui-row--attention";
    row.href = item.url || "/webapp/more";
    var body = document.createElement("span");
    body.className = "ui-row__main";
    body.appendChild(el("span", "ui-row__title", item.title || ""));
    if (item.meta) { body.appendChild(el("span", "ui-row__meta", item.meta)); }
    row.appendChild(body);
    if (item.count) { row.appendChild(el("span", "ui-badge", item.count)); }
    row.appendChild(el("span", "ui-row__chevron", "\\u203A"));
    return row;
  }

  function attentionBlock(items) {
    var section = el("section", "ui-section");
    section.id = "attention";
    var head = el("div", "ui-section__head");
    head.appendChild(el("h2", "ui-section__title", "Требует внимания"));
    section.appendChild(head);
    var list = el("div", "ui-list");
    list.id = "attention-list";
    items.forEach(function (item) { list.appendChild(attentionRow(item)); });
    section.appendChild(list);
    return section;
  }

  function todayCard(today) {
    var event = today.event || {};
    var card = el("div", "ui-card");
    card.id = "today";
    card.appendChild(el("span", "ui-card__label", today.label || "сегодня"));
    card.appendChild(el("h2", "ui-h2",
      (event.time ? event.time + " — " : "") + (event.title || "Событие")));
    card.appendChild(el("span", "ui-small ui-muted", event.place || ""));
    return card;
  }

  function weekCard(week) {
    var card = el("div", "ui-card");
    card.id = "week";
    var head = el("div", "ui-section__head");
    head.style.padding = "0";
    head.appendChild(el("h2", "ui-section__title", "Занятость на неделе"));
    var count = el("span", "ui-section__meta");
    count.id = "week-count";
    if (week.days) {
      count.textContent = "отмечено " + (week.marked || 0) + " из " + week.days;
    }
    head.appendChild(count);
    card.appendChild(head);
    // Пустая строка сохраняет высоту (1.45em из webapp-ui.css): иначе при
    // marked = 0 карточка сжимается уже после подмены.
    var meta = el("span", "ui-small ui-muted");
    meta.id = "week-meta";
    if (week.days) {
      if (week.marked >= week.days) {
        meta.textContent = "Неделя заполнена целиком. Можно поправить любую отметку.";
      } else if (week.marked) {
        meta.textContent = "Не отмечены " + (week.days - week.marked)
          + " дня. Пустая клетка — это «не знаю», а не «свободен».";
      }
    }
    card.appendChild(meta);
    var link = el("a", "ui-btn ui-btn--primary ui-btn--block", "Отметить занятость");
    link.href = "/webapp/week";
    card.appendChild(link);
    return card;
  }

  function debtCard(debt) {
    var card = el("div", "ui-card");
    card.id = "debt";
    card.appendChild(el("span", "ui-card__label", "взносы за " + (debt.period || "месяц")));
    card.appendChild(el("span", "ui-amount ui-amount--due", formatMoney(debt.rest)));
    card.appendChild(el("span", "ui-small ui-muted",
      "осталось внести из " + formatMoney(debt.due) + " \\u00B7 вносит организатор"));
    return card;
  }

  function formatMoney(value) {
    var digits = String(Math.round(Number(value) || 0))
      .replace(/\\B(?=(\\d{3})+(?!\\d))/g, "\\u00A0");
    return digits + "\\u00A0\\u20BD";
  }

  function personLines(node, data) {
    var name = el("h2", "ui-h2", data.name || "");
    name.id = "name";
    node.appendChild(name);
    // Подпись роли и дисциплины одной строкой: педагог видит, что он ведёт
    // (140 §4.2). Данные приходят в ответе, страница их не вычисляет.
    var role = data.role_label || "";
    var subjects = (data.disciplines || []).map(function (item) { return item.name; });
    if (subjects.length) { role += " · " + subjects.join(", "); }
    var line = el("div", "ui-small ui-muted", role);
    line.id = "role";
    node.appendChild(line);
  }

  function replace(node) {
    var current = main
      ? (main.querySelector("#home-loading") || main.querySelector(".ui-home"))
      : null;
    if (current) { main.replaceChild(node, current); }
  }

  function showQuickTx(data) {
    var quick = $("quick-tx");
    // Право уже проверила оболочка по `/webapp/nav`: помеченное `data-svoi-denied`
    // не открываем, даже если данные говорят «можно».
    if (quick && data.can_record && !quick.hasAttribute("data-svoi-denied")) {
      quick.hidden = false;
    }
  }

  function renderReady(data) {
    var node = root();
    personLines(node, data);
    var attention = data.attention || [];
    if (attention.length) { node.appendChild(attentionBlock(attention)); }
    if (data.today && data.today.event) { node.appendChild(todayCard(data.today)); }
    node.appendChild(weekCard(data.week || {}));
    var debt = data.debt || null;
    if (debt && debt.rest > 0) { node.appendChild(debtCard(debt)); }
    replace(node);
    showQuickTx(data);
  }

  function renderAlert(text, retry) {
    var node = root();
    var box = el("div", "ui-alert");
    box.appendChild(el("span", "ui-text", text));
    if (retry) {
      var again = el("button", "ui-btn", "Повторить");
      again.type = "button";
      again.onclick = function () { retryLoad(); };
      box.appendChild(again);
    }
    node.appendChild(box);
    node.appendChild(weekCard({}));
    replace(node);
    say(text);
  }

  // 5xx и обрыв: сообщение одно — баннер оболочки. Страница убирает скелеты и
  // оставляет правду без данных: знак и карточку недели с рабочей кнопкой.
  function renderBare() {
    var node = root();
    node.appendChild(weekCard({}));
    replace(node);
    say("");
  }

  function retryLoad() {
    if (!loadingTemplate) { location.reload(); return; }
    var current = main ? main.querySelector(".ui-home") : null;
    if (current) { main.replaceChild(loadingTemplate.cloneNode(true), current); }
    load();
  }

  async function load() {
    busy(true);
    say("Загружаем данные");
    try {
      var res = await fetch("/webapp/home/data");
      var data = await res.json().catch(function () { return {}; });
      if (res.status === 401) {
        renderAlert("Откройте приложение из бота — сессия не подтверждена.", false);
        return;
      }
      if (res.status >= 500) { renderBare(); return; }
      if (!res.ok || !data || data.ok !== true) {
        renderAlert("Не удалось загрузить. Потяните экран вниз, чтобы повторить.", true);
        return;
      }
      renderReady(data);
      say("");
    } catch (error) {
      // Текст общий на весь Mini App — «Сайт недоступен, попробуйте ещё раз.» —
      // его даёт баннер оболочки (webapp-shell.js); страница своё не дублирует
      // (ответ владельца на V-047, docs/reports/74-questions.md).
      renderBare();
    } finally {
      busy(false);
    }
  }

  busy(true);
  load();
})();
</script>
"""
